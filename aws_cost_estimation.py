import boto3
import json
import os
from dotenv import load_dotenv
from logger import setup_logger # Import the custom logger

# Initialize logger for this module
logger = setup_logger(name="CostEstimator")

# Load credentials from .env
load_dotenv()
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
logger.info(f"AWS_ACCESS_KEY Loaded: {'Yes' if AWS_ACCESS_KEY else 'No'}")
logger.info(f"AWS_SECRET_KEY Loaded: {'Yes' if AWS_SECRET_KEY else 'No'}")

REGION_CODE_MAP = {
                  "US East (N. Virginia)": "us-east-1",
                  "US East (Ohio)": "us-east-2",
                  "US West (N. California)": "us-west-1",
                  "US West (Oregon)": "us-west-2",
                  "Africa (Cape Town)": "af-south-1",
                  "Asia Pacific (Hong Kong)": "ap-east-1",
                  "Asia Pacific (Hyderabad)": "ap-south-2",
                  "Asia Pacific (Jakarta)": "ap-southeast-3",
                  "Asia Pacific (Melbourne)": "ap-southeast-4",
                  "Asia Pacific (Mumbai)": "ap-south-1",
                  "Asia Pacific (Osaka)": "ap-northeast-3",
                  "Asia Pacific (Seoul)": "ap-northeast-2",
                  "Asia Pacific (Singapore)": "ap-southeast-1",
                  "Asia Pacific (Sydney)": "ap-southeast-2",
                  "Asia Pacific (Tokyo)": "ap-northeast-1",
                  "Canada (Central)": "ca-central-1",
                  "Europe (Frankfurt)": "eu-central-1",
                  "Europe (Ireland)": "eu-west-1",
                  "Europe (London)": "eu-west-2",
                  "Europe (Milan)": "eu-south-1",
                  "Europe (Paris)": "eu-west-3",
                  "Europe (Spain)": "eu-south-2",
                  "Europe (Stockholm)": "eu-north-1",
                  "Europe (Zurich)": "eu-central-2",
                  "Middle East (Bahrain)": "me-south-1",
                  "Middle East (UAE)": "me-central-1",
                  "South America (São Paulo)": "sa-east-1",
                  "AWS GovCloud (US-East)": "us-gov-east-1",
                  "AWS GovCloud (US-West)": "us-gov-west-1"
                }

REGION_USAGE_TYPE_PREFIX = {
                              "ap-south-1": "APS3", # Mumbai
                              "ap-southeast-1": "APS1", # Singapore
                              "us-east-1": "USE1", # N. Virginia
                              "us-west-2": "USW2", # Oregon
                            }


def create_pricing_client():
    return boto3.client(
        'pricing',
        region_name='us-east-1',  # Pricing API is only in us-east-1
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )

def get_rds_cost_estimate(pricing_client,architecture_json):

    # Find RDS node
    rds_node = next((node for node in architecture_json['nodes'] if node['type'] == 'AmazonRDS'), None)
    if not rds_node:
        raise ValueError("No AmazonRDS node found in the architecture JSON.")

    region_friendly = rds_node['region']
    aws_region = REGION_CODE_MAP.get(region_friendly)
    if not aws_region:
        raise ValueError(f"Region '{region_friendly}' not mapped to AWS region code.")

    attributes = rds_node['attributes']
    instance_type = attributes['instanceType']
    db_engine = attributes['databaseEngine'].lower()
    term_type = attributes['termType']
    storage_gb = attributes['storageGB']
    storage_type = attributes['storageType']

    # Fetch RDS Instance Price 
    instance_filters = [
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": db_engine},
        {"Type": "TERM_MATCH", "Field": "deploymentOption", "Value": "Single-AZ"},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": aws_region},
        {"Type": "TERM_MATCH", "Field": "termType", "Value": term_type},
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Database Instance"},
    ]

    instance_price_response = pricing_client.get_products(
        ServiceCode='AmazonRDS',
        Filters=instance_filters,
        MaxResults=1
    )

    if not instance_price_response['PriceList']:
        raise ValueError("Could not fetch RDS instance pricing info.")

    price_item = json.loads(instance_price_response['PriceList'][0])

    # Get correct term level (e.g., "OnDemand" or "Reserved")
    term_type_key = list(price_item["terms"].keys())[0]  # usually 'OnDemand'
    term_data_map = price_item["terms"][term_type_key]
    first_term_id = next(iter(term_data_map))  # grab the first SKU id under OnDemand
    logger.debug(f"First SKU code for RDS instance: {first_term_id}")
    term_data = term_data_map[first_term_id]

    # Now get the price
    if "priceDimensions" not in term_data:
        raise ValueError("priceDimensions not found in RDS term data.")

    price_dimension = next(iter(term_data["priceDimensions"].values()))
    instance_price_per_hour = float(price_dimension["pricePerUnit"]["USD"])

    instance_price_per_hour = float(price_dimension['pricePerUnit']['USD'])
    monthly_instance_cost = round(instance_price_per_hour * 730, 4)

    #  Fetch RDS Storage Price 
    storage_filters = [
        {"Type": "TERM_MATCH", "Field": "serviceCode", "Value": "AmazonRDS"},
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Database Storage"},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": aws_region},
        {"Type": "TERM_MATCH", "Field": "volumeType", "Value": "General Purpose"},
        {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": db_engine},
        {"Type": "TERM_MATCH", "Field": "deploymentOption", "Value": "Single-AZ"},
         ]

    storage_price_response = pricing_client.get_products(
        ServiceCode='AmazonRDS',
        Filters=storage_filters,
        MaxResults=1
    )


    if not storage_price_response['PriceList']:
        raise ValueError("Could not fetch RDS storage pricing info.")

    storage_item = json.loads(storage_price_response['PriceList'][0])
    storage_term_data = next(iter(storage_item['terms']['OnDemand'].values()))
    storage_price_dimension = next(iter(storage_term_data['priceDimensions'].values()))
    storage_price_per_gb = float(storage_price_dimension['pricePerUnit']['USD'])
    monthly_storage_cost = round(storage_price_per_gb * storage_gb, 4)

    # --- 3. Total Cost ---
    total_rds_monthly_cost = round(monthly_instance_cost + monthly_storage_cost, 4)

    logger.info(f"RDS_Monthly_Instance_cost= {monthly_instance_cost}")
    logger.info(f"RDS_Monthly_Storage_cost= {monthly_storage_cost}")
    logger.info(f"Total_RDS_Monthly_cost= {total_rds_monthly_cost}")
    logger.info("--------------------------------------------------------------------")
    return {
        "rds_instance_monthly_usd": monthly_instance_cost,
        "rds_storage_monthly_usd": monthly_storage_cost,
        "rds_total_monthly_usd": total_rds_monthly_cost
    }

def get_ec2_cost_estimate(pricing_client, architecture_json):
  #  Find EC2 node 
        ec2_node = next((node for node in architecture_json['nodes'] if node['type'] == 'AmazonEC2'), None)
        if not ec2_node:
            raise ValueError("No AmazonEC2 node found in the architecture JSON.")

        region_friendly = ec2_node['region']
        region = region_friendly  # For EC2 pricing API, region is used as location (full name)

        attributes = ec2_node['attributes']
        instance_type = attributes.get("instanceType", "t3.micro")
        operating_system = attributes.get("operatingSystem", "Linux")
        tenancy = attributes.get("tenancy", "Shared")
        capacity_status = attributes.get("capacitystatus", "Used")
        pre_installed_sw = attributes.get("preInstalledSw", "NA")
        term_type = attributes.get("termType", "OnDemand")

  #  Build EC2 pricing filters 
        ec2_filters = [
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": operating_system},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": tenancy},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": capacity_status},
            {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": pre_installed_sw},
            {"Type": "TERM_MATCH", "Field": "termType", "Value": term_type},
            {"Type": "TERM_MATCH", "Field": "location", "Value": region}
        ]

  #  Query Pricing API 
        ec2_price_response = pricing_client.get_products(
            ServiceCode='AmazonEC2',
            Filters=ec2_filters,
            MaxResults=1
        )

        if not ec2_price_response['PriceList']:
            raise ValueError("Could not fetch EC2 instance pricing info.")

        price_item = json.loads(ec2_price_response['PriceList'][0])
        term_type_key = list(price_item['terms'].keys())[0]  # 'OnDemand' or 'Reserved'
        term_data_map = price_item['terms'][term_type_key]
        first_term_id = next(iter(term_data_map))
        term_data = term_data_map[first_term_id]

        if "priceDimensions" not in term_data:
            raise ValueError("priceDimensions not found in EC2 term data.")

        price_dimension = next(iter(term_data["priceDimensions"].values()))
        instance_price_per_hour = float(price_dimension["pricePerUnit"]["USD"])
        monthly_instance_cost = round(instance_price_per_hour * 730, 4)

        
        

  # Start of EBS calculation block      
        storage_gb = attributes.get("storageGB", 30)  # default EBS size
        volume_type = attributes.get("volumeType", "gp3")  # typical default
        

  # Build EBS (EC2 storage) pricing filter
        storage_filters = [
                            {"Type": "TERM_MATCH", "Field": "serviceCode", "Value": "AmazonEC2"},
                            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
                            {"Type": "TERM_MATCH", "Field": "location", "Value": region},
                            {"Type": "TERM_MATCH", "Field": "volumeApiName", "Value": volume_type},
                                 
                          ]
  #  Query Pricing API 
        ebs_price_response = pricing_client.get_products(
            ServiceCode='AmazonEC2',
            Filters=storage_filters,
            MaxResults=1
        )
  # logger.debug(json.dumps(ebs_price_response, indent=2))

        if not ebs_price_response['PriceList']:
            raise ValueError("Could not fetch EC2 storage (EBS) pricing info.")

        storage_item = json.loads(ebs_price_response['PriceList'][0])
        term_type_key = list(storage_item['terms'].keys())[0]  # 'OnDemand'
        term_data_map = storage_item['terms'][term_type_key]
        first_term_id = next(iter(term_data_map))
        term_data = term_data_map[first_term_id]

        if "priceDimensions" not in term_data:
            raise ValueError("priceDimensions not found in EC2 storage term data.")

        price_dimension = next(iter(term_data["priceDimensions"].values()))
        storage_price_per_gb = float(price_dimension["pricePerUnit"]["USD"])
        monthly_storage_cost = round(storage_price_per_gb * storage_gb, 4)
        total_ec2_monthly_cost = round(monthly_instance_cost + monthly_storage_cost, 3)

        logger.info(f"monthly EC2 instance cost = {monthly_instance_cost}")
        logger.info(f"monthly EC2 EBS Storage cost = {monthly_storage_cost}")
        logger.info(f"Total Monthly EC2 Cost =  {total_ec2_monthly_cost}")
        logger.info("--------------------------------------------------------------------")
        return {
            "ec2_instance_monthly_usd": monthly_instance_cost,
            "ec2_storage_monthly_usd": monthly_storage_cost,
            "ec2_total_monthly_usd": total_ec2_monthly_cost
        }

def get_lambda_cost_estimate(pricing_client, architecture_json):
    # Find Lambda node
    lambda_node = next((node for node in architecture_json['nodes'] if node['type'] == 'AWSLambda'), None)
    if not lambda_node:
        raise ValueError("No AWSLambda node found in the architecture JSON.")

    region_friendly = lambda_node['region']
    aws_region = REGION_CODE_MAP.get(region_friendly.strip())
    aws_region_prefix = REGION_USAGE_TYPE_PREFIX.get(aws_region.strip())


    attributes = lambda_node['attributes']
    requests_per_month = attributes.get("requestsPerMonth", 1000000)
    memory_mb = attributes.get("memorySizeMB", 128)
    duration_ms = attributes.get("durationMs", 100)
    
    if not aws_region_prefix:
       raise ValueError(f"Region prefix for '{aws_region}' not found.")

    # Lambda pricing filters for compute (GB-second)
    compute_filters = [
                      {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Serverless"},
                      {"Type": "TERM_MATCH", "Field": "regionCode", "Value": aws_region},
                      {"Type": "TERM_MATCH", "Field": "usagetype", "Value": f"{aws_region_prefix}-Lambda-GB-Second"},
                  ]


    compute_price_response = pricing_client.get_products(
        ServiceCode="AWSLambda",
        Filters=compute_filters,
        MaxResults=1
    )
    # logger.debug( compute_price_response)
    if not compute_price_response['PriceList']:
        raise ValueError("Could not fetch Lambda compute pricing info.")

    compute_item = json.loads(compute_price_response['PriceList'][0])
    term_type_key = list(compute_item['terms'].keys())[0]
    term_data_map = compute_item['terms'][term_type_key]
    first_term_id = next(iter(term_data_map))
    term_data = term_data_map[first_term_id]

    if "priceDimensions" not in term_data:
        raise ValueError("priceDimensions not found in Lambda compute term data.")

    compute_price_dimension = next(iter(term_data["priceDimensions"].values()))
    price_per_gb_second = float(compute_price_dimension["pricePerUnit"]["USD"])
    
    # Lambda pricing filters for requests
    request_filters = [
                      {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Serverless"},
                      {"Type": "TERM_MATCH", "Field": "regionCode", "Value": "ap-south-1"},
                      {"Type": "TERM_MATCH", "Field": "usagetype", "Value": "APS3-Request"},
                      ]
     

    request_price_response = pricing_client.get_products(
        ServiceCode="AWSLambda",
        Filters=request_filters,
        MaxResults=1
    )
#    response = pricing_client.get_products(
#    ServiceCode="AWSLambda",
#    Filters=[
#        {"Type": "TERM_MATCH", "Field": "location", "Value": region_friendly},
#    ],
#    MaxResults=100
#    )
#
#    for p in response['PriceList']:
#        product = json.loads(p)
#        logger.debug(json.dumps(product['product']['attributes'], indent=2))

    #logger.debug(request_price_response['PriceList'])
    if not request_price_response['PriceList']:
        raise ValueError("Could not fetch Lambda request pricing info.")

    request_item = json.loads(request_price_response['PriceList'][0])
    term_data_map = request_item['terms'][term_type_key]
    first_term_id = next(iter(term_data_map))
    request_term_data = term_data_map[first_term_id]

    if "priceDimensions" not in request_term_data:
        raise ValueError("priceDimensions not found in Lambda request term data.")

    request_price_dimension = next(iter(request_term_data["priceDimensions"].values()))
    price_per_request = float(request_price_dimension["pricePerUnit"]["USD"])

    # Compute cost calculation
    total_gb_seconds = (duration_ms / 1000) * (memory_mb / 1024) * requests_per_month
    total_compute_cost = round(price_per_gb_second * total_gb_seconds, 4)

    billable_requests = max(0, requests_per_month - 1000000)
    total_request_cost = round(price_per_request * billable_requests, 4)
    total_lambda_cost = round(total_compute_cost + total_request_cost, 4)
    logger.info(f"monthly Lambda compute cost = {total_compute_cost}")
    logger.info(f"monthly Lambda request cost = {total_request_cost}")
    logger.info(f"lambda_monthly_total_usd: {total_lambda_cost}")
    logger.info("--------------------------------------------------------------------")

    return {
        "lambda_compute_monthly_usd": total_compute_cost,
        "lambda_request_monthly_usd": total_request_cost
    }

def get_s3_cost_estimate(pricing_client, architecture_json):

    REGION_CODE_MAP = {
        "Asia Pacific (Mumbai)": "ap-south-1",
        "US East (N. Virginia)": "us-east-1",
        "Asia Pacific (Singapore)": "ap-southeast-1",
        # Add more as needed
    }
    REGION_USAGE_TYPE_PREFIX = {
                              "ap-south-1": "APS3",
                              "ap-southeast-1": "APS1",
                              "us-east-1": "USE1",
                              "us-west-2": "USW2",
                              
                            }

    #  Parse the S3 node from the architecture JSON
    s3_node = next((node for node in architecture_json['nodes'] if node['type'] == 'AmazonS3'), None)
    if not s3_node:
        raise ValueError("No AmazonS3 node found in the architecture JSON.")

    region_friendly = s3_node['region']
    aws_region = REGION_CODE_MAP.get(region_friendly)
    if not aws_region:
        raise ValueError(f"Region '{region_friendly}' not mapped to AWS region code.")

    attributes = s3_node['attributes']
    storage_gb = attributes.get('storageGB', 100)
    storage_class = attributes.get('storageClass', 'Standard')
    num_put_requests = attributes.get('numPUTRequests', 1000)
    num_get_requests = attributes.get('numGETRequests', 10000)

    usage_prefix = REGION_USAGE_TYPE_PREFIX.get(aws_region)
    if not usage_prefix:
        raise ValueError(f"Usage prefix for region '{aws_region}' not found.")

    #  Storage Cost 
    storage_filters = [
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
        {"Type": "TERM_MATCH", "Field": "location", "Value": region_friendly},
        {"Type": "TERM_MATCH", "Field": "storageClass", "Value": storage_class},
        {"Type": "TERM_MATCH", "Field": "usagetype", "Value": f"{usage_prefix}-TimedStorage-ByteHrs"},
    ]

#    response = pricing_client.get_products(
#        ServiceCode="AmazonS3",
#        Filters=[
#            {"Type": "TERM_MATCH", "Field": "location", "Value": region_friendly},
#        ],
#        MaxResults=100
#    )
#
#    for p in response['PriceList']:
#            product = json.loads(p)
#            logger.debug(json.dumps(product['product']['attributes'], indent=2))

#    logger.debug(request_price_response['PriceList'])

    storage_price_response = pricing_client.get_products(
        ServiceCode='AmazonS3',
        Filters=storage_filters,
        MaxResults=1
    )
    logger.debug(json.dumps(storage_price_response))
    if not storage_price_response['PriceList']:
        raise ValueError("Could not fetch S3 storage pricing info.")

    storage_item = json.loads(storage_price_response['PriceList'][0])
    term_type_key = list(storage_item['terms'].keys())[0]
    term_data_map = storage_item['terms'][term_type_key]
    first_term_id = next(iter(term_data_map))
    term_data = term_data_map[first_term_id]
    price_dimension = next(iter(term_data["priceDimensions"].values()))
    price_per_gb_month = float(price_dimension["pricePerUnit"]["USD"])
    monthly_storage_cost = round(storage_gb * price_per_gb_month, 4)

    #  PUT Request Cost 
    put_filters = [
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Requests"},
        {"Type": "TERM_MATCH", "Field": "operation", "Value": "PutObject"},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": aws_region},
    ]

    put_price_response = pricing_client.get_products(
        ServiceCode='AmazonS3',
        Filters=put_filters,
        MaxResults=1
    )

    put_price = 0.0
    if put_price_response['PriceList']:
        put_item = json.loads(put_price_response['PriceList'][0])
        term_data_map = put_item['terms'][term_type_key]
        first_term_id = next(iter(term_data_map))
        term_data = term_data_map[first_term_id]
        price_dimension = next(iter(term_data["priceDimensions"].values()))
        put_price = float(price_dimension["pricePerUnit"]["USD"])
    monthly_put_cost = round(put_price * num_put_requests, 4)

    #  GET Request Cost 
    get_filters = [
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Requests"},
        {"Type": "TERM_MATCH", "Field": "operation", "Value": "GetObject"},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": aws_region},
    ]

    get_price_response = pricing_client.get_products(
        ServiceCode='AmazonS3',
        Filters=get_filters,
        MaxResults=1
    )

    get_price = 0.0
    if get_price_response['PriceList']:
        get_item = json.loads(get_price_response['PriceList'][0])
        term_data_map = get_item['terms'][term_type_key]
        first_term_id = next(iter(term_data_map))
        term_data = term_data_map[first_term_id]
        price_dimension = next(iter(term_data["priceDimensions"].values()))
        get_price = float(price_dimension["pricePerUnit"]["USD"])
    monthly_get_cost = round(get_price * num_get_requests, 4)

    #  Total S3 Cost 
    total_s3_cost = round(monthly_storage_cost + monthly_put_cost + monthly_get_cost, 4)

    logger.info(f"S3_Monthly_Storage_cost = {monthly_storage_cost}")
    logger.info(f"S3_Monthly_PUT_cost = {monthly_put_cost}")
    logger.info(f"S3_Monthly_GET_cost = {monthly_get_cost}")
    logger.info(f"Total_S3_Monthly_cost = {total_s3_cost}")
    logger.info("--------------------------------------------------------------------")

    return {
        "s3_storage_monthly_usd": monthly_storage_cost,
        "s3_put_request_monthly_usd": monthly_put_cost,
        "s3_get_request_monthly_usd": monthly_get_cost,
        "s3_total_monthly_usd": total_s3_cost
    }
#def get_iam_cost_estimate(pricing_client,architecture_json):
    

if  __name__ == "__main__":
    architecture_json = {
              "title": "Cost_Estimation_Ready_Architecture",
              "nodes": [
                {
                  "id": "webAppServer",
                  "type": "AmazonEC2",
                  "label": "Web Server",
                  "region": "Asia Pacific (Mumbai)",
                  "attributes": {
                    "instanceType": "t3.micro",
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "capacitystatus": "Used",
                    "preInstalledSw": "NA",
                    "termType": "OnDemand",
                    "storageGB": 15,
                    "volumeType": "gp3"

                  }
                },
                {
                  "id": "database",
                  "type": "AmazonRDS",
                  "label": "RDS Database",
                  "region": "Asia Pacific (Mumbai)",
                  "attributes": {
                    "instanceType": "db.t3.micro",
                    "databaseEngine": "PostgreSQL",
                    "termType": "OnDemand",
                    "storageGB": 100,
                    "storageType": "gp3"
                  }
                },
                {
                  "id": "storageBucket",
                  "type": "AmazonS3",
                  "label": "S3 Bucket",
                  "region": "Asia Pacific (Mumbai)",
                  "attributes": {
                    "storageGB": 100,
                    "storageClass": "Standard",
                    "numPUTRequests": 10000,
                    "numGETRequests": 50000
                  }
                },
                {
                  "id": "cloudfrontCDN",
                  "type": "AmazonCloudFront",
                  "label": "CloudFront CDN",
                  "region": "Global",
                  "attributes": {
                    "dataOutGB": 100
                  }
                },
              {
                  "id": "lambdaFunction",
                  "type": "AWSLambda",
                  "label": "Lambda Function",
                  "region": "Asia Pacific (Mumbai)",
                  "attributes": {
                    "requestsPerMonth": 10000000,
                    "durationMs": 100,
                    "memorySizeMB": 128
                  }
                },
                {
                  "id": "iamRole",
                  "type": "AWSIAM",
                  "label": "IAM Role",
                  "region": "Global",
                  "attributes": {
                    "userCount": 5,
                    "policyType": "Managed"
                  }
                },
              ],
              "edges": [
                { "from": "cloudfrontCDN", "to": "storageBucket" },
                { "from": "webAppServer", "to": "database" },
                { "from": "webAppServer", "to": "lambdaFunction" },
                { "from": "lambdaFunction", "to": "database" },
                { "from": "iamRole", "to": "webAppServer" },
                { "from": "iamRole", "to": "lambdaFunction" }
              ]
            }

    pricing_client = create_pricing_client()
    get_rds_cost_estimate(pricing_client,architecture_json)
    get_ec2_cost_estimate(pricing_client,architecture_json)
    get_lambda_cost_estimate(pricing_client,architecture_json)
#   get_s3_cost_estimate(pricing_client,architecture_json)