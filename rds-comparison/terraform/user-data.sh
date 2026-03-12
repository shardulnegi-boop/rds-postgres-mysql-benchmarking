#!/bin/bash
set -euo pipefail
exec > /var/log/user-data.log 2>&1

echo "=== Aurora Benchmark EC2 Bootstrap ==="

dnf update -y
dnf install -y python3.11 python3.11-pip postgresql16 mysql jq htop sysstat

pip3.11 install --no-cache-dir \
  psycopg2-binary pymysql boto3 matplotlib

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
export PG_WRITER_ID="aurora-bench-pg-writer"
export PG_READER_IDS="aurora-bench-pg-reader-1,aurora-bench-pg-reader-2"
export MYSQL_WRITER_ID="aurora-bench-mysql-writer"
export MYSQL_READER_IDS="aurora-bench-mysql-reader-1,aurora-bench-mysql-reader-2"
ENVEOF

chmod 600 /home/ec2-user/db_config.env
chown ec2-user:ec2-user /home/ec2-user/db_config.env

mkdir -p /home/ec2-user/{benchmark,results}
chown -R ec2-user:ec2-user /home/ec2-user/{benchmark,results}

cat > /home/ec2-user/check_ready.sh << 'CHECKEOF'
#!/bin/bash
source /home/ec2-user/db_config.env
echo "Checking PostgreSQL..."
pg_isready -h "$PG_HOST" -p 5432 -U "$DB_USER" && echo "  Writer: OK" || echo "  Writer: NOT READY"
pg_isready -h "$PG_READER_HOST" -p 5432 -U "$DB_USER" && echo "  Reader: OK" || echo "  Reader: NOT READY"
echo "Checking MySQL..."
mysql -h "$MYSQL_HOST" -u "$DB_USER" -p"$DB_PASS" -e "SELECT 1" 2>/dev/null && echo "  Writer: OK" || echo "  Writer: NOT READY"
mysql -h "$MYSQL_READER_HOST" -u "$DB_USER" -p"$DB_PASS" -e "SELECT 1" 2>/dev/null && echo "  Reader: OK" || echo "  Reader: NOT READY"
CHECKEOF
chmod +x /home/ec2-user/check_ready.sh
chown ec2-user:ec2-user /home/ec2-user/check_ready.sh

echo "=== Bootstrap Complete ==="
