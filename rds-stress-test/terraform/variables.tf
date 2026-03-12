variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "aurora_instance_class" {
  description = "Aurora instance type for both clusters"
  type        = string
  default     = "db.r7g.4xlarge"
}

variable "ec2_instance_type" {
  description = "EC2 instance for load generation"
  type        = string
  default     = "c7g.4xlarge"
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
  description = "SSH public key content"
  type        = string
}
