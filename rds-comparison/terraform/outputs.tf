output "pg_writer_endpoint" {
  value = aws_rds_cluster.pg.endpoint
}

output "pg_reader_endpoint" {
  value = aws_rds_cluster.pg.reader_endpoint
}

output "pg_reader_instance_endpoints" {
  value = [for i in aws_rds_cluster_instance.pg_readers : i.endpoint]
}

output "mysql_writer_endpoint" {
  value = aws_rds_cluster.mysql.endpoint
}

output "mysql_reader_endpoint" {
  value = aws_rds_cluster.mysql.reader_endpoint
}

output "mysql_reader_instance_endpoints" {
  value = [for i in aws_rds_cluster_instance.mysql_readers : i.endpoint]
}

output "ec2_public_ip" {
  value = aws_instance.loadgen.public_ip
}

output "ssh_command" {
  value = "ssh -i <your-private-key> ec2-user@${aws_instance.loadgen.public_ip}"
}

output "instance_ids" {
  description = "All Aurora instance identifiers for CloudWatch"
  value = {
    pg_writer      = aws_rds_cluster_instance.pg_writer.identifier
    pg_readers     = [for i in aws_rds_cluster_instance.pg_readers : i.identifier]
    mysql_writer   = aws_rds_cluster_instance.mysql_writer.identifier
    mysql_readers  = [for i in aws_rds_cluster_instance.mysql_readers : i.identifier]
  }
}

output "estimated_cost" {
  value = "~$10 total (6x Aurora db.r7g.4xlarge + 1x EC2 c7g.4xlarge for ~45 min)"
}
