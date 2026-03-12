##############################################################################
# Aurora MySQL vs PostgreSQL Benchmark — Full Infrastructure
# 1 Writer + 2 Readers per engine, Enhanced Monitoring, Performance Insights
# Designed for a 30-min stress test, then destroy everything.
##############################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ──────────────────────────────────────────────────────────
# VPC & Networking
# ──────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "aurora-benchmark-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "aurora-benchmark-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "aurora-bench-public" }
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.10.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]
  tags              = { Name = "aurora-bench-private-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.11.0/24"
  availability_zone = data.aws_availability_zones.available.names[1]
  tags              = { Name = "aurora-bench-private-b" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "aurora-bench-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ──────────────────────────────────────────────────────────
# Security Groups
# ──────────────────────────────────────────────────────────

resource "aws_security_group" "ec2" {
  name_prefix = "aurora-bench-ec2-"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "aurora-bench-ec2-sg" }
}

resource "aws_security_group" "aurora" {
  name_prefix = "aurora-bench-db-"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MySQL from EC2"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
  }

  ingress {
    description     = "PostgreSQL from EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "aurora-bench-db-sg" }
}

# ──────────────────────────────────────────────────────────
# IAM — Enhanced Monitoring for RDS
# ──────────────────────────────────────────────────────────

resource "aws_iam_role" "rds_monitoring" {
  name = "aurora-bench-rds-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ──────────────────────────────────────────────────────────
# IAM — EC2 Instance Profile (CloudWatch read access)
# ──────────────────────────────────────────────────────────

resource "aws_iam_role" "ec2_benchmark" {
  name = "aurora-bench-ec2"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ec2_cloudwatch" {
  name = "cloudwatch-read"
  role = aws_iam_role.ec2_benchmark.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = [
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics"
      ]
      Effect   = "Allow"
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "ec2_benchmark" {
  name = "aurora-bench-ec2"
  role = aws_iam_role.ec2_benchmark.name
}

# ──────────────────────────────────────────────────────────
# Aurora DB Subnet Group
# ──────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "aurora" {
  name       = "aurora-benchmark"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  tags       = { Name = "aurora-benchmark-subnet-group" }
}

# ──────────────────────────────────────────────────────────
# Aurora PostgreSQL — 1 Writer + 2 Readers
# ──────────────────────────────────────────────────────────

resource "aws_rds_cluster" "pg" {
  cluster_identifier      = "aurora-bench-pg"
  engine                  = "aurora-postgresql"
  engine_version          = "16.11"
  master_username         = var.db_username
  master_password         = var.db_password
  database_name           = "benchmark"
  db_subnet_group_name    = aws_db_subnet_group.aurora.name
  vpc_security_group_ids  = [aws_security_group.aurora.id]
  storage_type            = "aurora-iopt1"
  backup_retention_period = 1
  skip_final_snapshot     = true
  apply_immediately       = true
  tags                    = { Name = "aurora-bench-pg" }
}

resource "aws_rds_cluster_instance" "pg_writer" {
  identifier                   = "aurora-bench-pg-writer"
  cluster_identifier           = aws_rds_cluster.pg.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.pg.engine
  engine_version               = aws_rds_cluster.pg.engine_version
  performance_insights_enabled = true
  monitoring_interval          = 15
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "aurora-bench-pg-writer" }
}

resource "aws_rds_cluster_instance" "pg_readers" {
  count                        = 2
  identifier                   = "aurora-bench-pg-reader-${count.index + 1}"
  cluster_identifier           = aws_rds_cluster.pg.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.pg.engine
  engine_version               = aws_rds_cluster.pg.engine_version
  performance_insights_enabled = true
  monitoring_interval          = 15
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "aurora-bench-pg-reader-${count.index + 1}" }
}

# ──────────────────────────────────────────────────────────
# Aurora MySQL — 1 Writer + 2 Readers
# ──────────────────────────────────────────────────────────

resource "aws_rds_cluster" "mysql" {
  cluster_identifier      = "aurora-bench-mysql"
  engine                  = "aurora-mysql"
  engine_version          = "8.0.mysql_aurora.3.08.0"
  master_username         = var.db_username
  master_password         = var.db_password
  database_name           = "benchmark"
  db_subnet_group_name    = aws_db_subnet_group.aurora.name
  vpc_security_group_ids  = [aws_security_group.aurora.id]
  storage_type            = "aurora-iopt1"
  backup_retention_period = 1
  skip_final_snapshot     = true
  apply_immediately       = true
  tags                    = { Name = "aurora-bench-mysql" }
}

resource "aws_rds_cluster_instance" "mysql_writer" {
  identifier                   = "aurora-bench-mysql-writer"
  cluster_identifier           = aws_rds_cluster.mysql.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.mysql.engine
  engine_version               = aws_rds_cluster.mysql.engine_version
  performance_insights_enabled = true
  monitoring_interval          = 15
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "aurora-bench-mysql-writer" }
}

resource "aws_rds_cluster_instance" "mysql_readers" {
  count                        = 2
  identifier                   = "aurora-bench-mysql-reader-${count.index + 1}"
  cluster_identifier           = aws_rds_cluster.mysql.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.mysql.engine
  engine_version               = aws_rds_cluster.mysql.engine_version
  performance_insights_enabled = true
  monitoring_interval          = 15
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "aurora-bench-mysql-reader-${count.index + 1}" }
}

# ──────────────────────────────────────────────────────────
# EC2 Load Generator (same AZ as Aurora for min latency)
# ──────────────────────────────────────────────────────────

data "aws_ami" "al2023_arm" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_key_pair" "benchmark" {
  key_name   = "aurora-benchmark-key"
  public_key = var.ssh_public_key
}

resource "aws_instance" "loadgen" {
  ami                         = data.aws_ami.al2023_arm.id
  instance_type               = var.ec2_instance_type
  key_name                    = aws_key_pair.benchmark.key_name
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.ec2.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_benchmark.name
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/user-data.sh", {
    pg_host          = aws_rds_cluster.pg.endpoint
    pg_reader_host   = aws_rds_cluster.pg.reader_endpoint
    mysql_host       = aws_rds_cluster.mysql.endpoint
    mysql_reader_host = aws_rds_cluster.mysql.reader_endpoint
    db_user          = var.db_username
    db_pass          = var.db_password
    db_name          = "benchmark"
    aws_region       = var.region
  })

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
    throughput  = 1000
    iops        = 16000
  }

  tags = { Name = "aurora-benchmark-loadgen" }

  depends_on = [
    aws_rds_cluster_instance.pg_writer,
    aws_rds_cluster_instance.pg_readers,
    aws_rds_cluster_instance.mysql_writer,
    aws_rds_cluster_instance.mysql_readers,
  ]
}
