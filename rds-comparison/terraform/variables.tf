variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "aurora_instance_class" {
  description = "Aurora instance type for both clusters"
  type        = string
  default     = "db.r7g.4xlarge" # 16 vCPU, 128GB RAM, up to 15 Gbps network
}

variable "ec2_instance_type" {
  description = "EC2 instance for load generation"
  type        = string
  default     = "c7g.4xlarge" # 16 vCPU, 32GB RAM — enough to generate 1 GB/s
}

variable "db_username" {
  description = "Master username for both Aurora clusters"
  type        = string
  default     = "benchadmin"
}

variable "db_password" {
  description = "Master password for both Aurora clusters"
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "SSH public key content (cat ~/.ssh/id_rsa.pub)"
  type        = string
}

variable "ssh_cidr" {
  description = "CIDR block allowed to SSH into EC2 (your IP/32)"
  type        = string
  default     = "0.0.0.0/0"
}
