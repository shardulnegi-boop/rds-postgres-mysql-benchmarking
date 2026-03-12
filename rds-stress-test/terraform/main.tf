##############################################################################
# Aurora Stress Test v2 — Break the Database
# 1 Writer + 2 Readers per engine, publicly accessible, ramp-to-failure
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
# VPC & Networking (all public for simplicity)
# ──────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "stress-test-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "stress-test-igw" }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "stress-test-public-a" }
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = true
  tags                    = { Name = "stress-test-public-b" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "stress-test-public-rt" }
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}

# ──────────────────────────────────────────────────────────
# Security Groups — wide open for stress test
# ──────────────────────────────────────────────────────────

resource "aws_security_group" "ec2" {
  name_prefix = "stress-ec2-"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "stress-test-ec2-sg" }
}

resource "aws_security_group" "aurora" {
  name_prefix = "stress-db-"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "MySQL from anywhere"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "PostgreSQL from anywhere"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "stress-test-db-sg" }
}

# ──────────────────────────────────────────────────────────
# IAM — Enhanced Monitoring
# ──────────────────────────────────────────────────────────

resource "aws_iam_role" "rds_monitoring" {
  name = "stress-test-rds-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ──────────────────────────────────────────────────────────
# IAM — EC2 Instance Profile (CloudWatch read)
# ──────────────────────────────────────────────────────────

resource "aws_iam_role" "ec2_stress" {
  name = "stress-test-ec2"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ec2_cloudwatch" {
  name = "cloudwatch-read"
  role = aws_iam_role.ec2_stress.id

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

resource "aws_iam_instance_profile" "ec2_stress" {
  name = "stress-test-ec2"
  role = aws_iam_role.ec2_stress.name
}

# ──────────────────────────────────────────────────────────
# Aurora DB Subnet Group (public subnets)
# ──────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "aurora" {
  name       = "stress-test-aurora"
  subnet_ids = [aws_subnet.public_a.id, aws_subnet.public_b.id]
  tags       = { Name = "stress-test-subnet-group" }
}

# ──────────────────────────────────────────────────────────
# Aurora PostgreSQL — 1 Writer + 2 Readers (PUBLIC)
# ──────────────────────────────────────────────────────────

resource "aws_rds_cluster" "pg" {
  cluster_identifier      = "stress-pg"
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
  tags                    = { Name = "stress-pg" }
}

resource "aws_rds_cluster_instance" "pg_writer" {
  identifier                   = "stress-pg-writer"
  cluster_identifier           = aws_rds_cluster.pg.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.pg.engine
  engine_version               = aws_rds_cluster.pg.engine_version
  publicly_accessible          = true
  performance_insights_enabled = true
  monitoring_interval          = 5
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "stress-pg-writer" }
}

resource "aws_rds_cluster_instance" "pg_readers" {
  count                        = 2
  identifier                   = "stress-pg-reader-${count.index + 1}"
  cluster_identifier           = aws_rds_cluster.pg.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.pg.engine
  engine_version               = aws_rds_cluster.pg.engine_version
  publicly_accessible          = true
  performance_insights_enabled = true
  monitoring_interval          = 5
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "stress-pg-reader-${count.index + 1}" }
}

# ──────────────────────────────────────────────────────────
# Aurora MySQL — 1 Writer + 2 Readers (PUBLIC)
# ──────────────────────────────────────────────────────────

resource "aws_rds_cluster" "mysql" {
  cluster_identifier      = "stress-mysql"
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
  tags                    = { Name = "stress-mysql" }
}

resource "aws_rds_cluster_instance" "mysql_writer" {
  identifier                   = "stress-mysql-writer"
  cluster_identifier           = aws_rds_cluster.mysql.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.mysql.engine
  engine_version               = aws_rds_cluster.mysql.engine_version
  publicly_accessible          = true
  performance_insights_enabled = true
  monitoring_interval          = 5
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "stress-mysql-writer" }
}

resource "aws_rds_cluster_instance" "mysql_readers" {
  count                        = 2
  identifier                   = "stress-mysql-reader-${count.index + 1}"
  cluster_identifier           = aws_rds_cluster.mysql.id
  instance_class               = var.aurora_instance_class
  engine                       = aws_rds_cluster.mysql.engine
  engine_version               = aws_rds_cluster.mysql.engine_version
  publicly_accessible          = true
  performance_insights_enabled = true
  monitoring_interval          = 5
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn
  tags                         = { Name = "stress-mysql-reader-${count.index + 1}" }
}

# ──────────────────────────────────────────────────────────
# EC2 Load Generator
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

resource "aws_key_pair" "stress" {
  key_name   = "stress-test-key"
  public_key = var.ssh_public_key
}

resource "aws_instance" "loadgen" {
  ami                         = data.aws_ami.al2023_arm.id
  instance_type               = var.ec2_instance_type
  key_name                    = aws_key_pair.stress.key_name
  subnet_id                   = aws_subnet.public_a.id
  vpc_security_group_ids      = [aws_security_group.ec2.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_stress.name
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/user-data.sh", {
    pg_host           = aws_rds_cluster.pg.endpoint
    pg_reader_host    = aws_rds_cluster.pg.reader_endpoint
    mysql_host        = aws_rds_cluster.mysql.endpoint
    mysql_reader_host = aws_rds_cluster.mysql.reader_endpoint
    db_user           = var.db_username
    db_pass           = var.db_password
    db_name           = "benchmark"
    aws_region        = var.region
  })

  root_block_device {
    volume_size = 100
    volume_type = "gp3"
    throughput  = 500
    iops        = 3000
  }

  tags = { Name = "stress-test-loadgen" }

  depends_on = [
    aws_rds_cluster_instance.pg_writer,
    aws_rds_cluster_instance.pg_readers,
    aws_rds_cluster_instance.mysql_writer,
    aws_rds_cluster_instance.mysql_readers,
  ]
}
