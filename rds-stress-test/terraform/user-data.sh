#!/bin/bash
set -euo pipefail
exec > /var/log/user-data.log 2>&1

echo "=== Stress Test EC2 Bootstrap ==="

dnf update -y
dnf install -y python3.11 python3.11-pip postgresql16 mariadb105 jq htop sysstat

pip3.11 install --no-cache-dir \
  psycopg2-binary pymysql boto3

# Connection config
cat > /home/ec2-user/db_config.env << 'ENVEOF'
export PG_HOST="${pg_host}"
export PG_READER_HOST="${pg_reader_host}"
export MYSQL_HOST="${mysql_host}"
export MYSQL_READER_HOST="${mysql_reader_host}"
export DB_USER="${db_user}"
export DB_PASS="${db_pass}"
export DB_NAME="${db_name}"
export AWS_REGION="${aws_region}"
ENVEOF

chmod 600 /home/ec2-user/db_config.env
chown ec2-user:ec2-user /home/ec2-user/db_config.env

mkdir -p /home/ec2-user/{benchmark,results}
mkdir -p /data
chown -R ec2-user:ec2-user /home/ec2-user/{benchmark,results} /data

echo "=== Bootstrap Complete ==="
