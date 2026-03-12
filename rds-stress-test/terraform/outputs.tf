output "pg_writer_endpoint" {
  value = aws_rds_cluster.pg.endpoint
}

output "pg_reader_endpoint" {
  value = aws_rds_cluster.pg.reader_endpoint
}

output "mysql_writer_endpoint" {
  value = aws_rds_cluster.mysql.endpoint
}

output "mysql_reader_endpoint" {
  value = aws_rds_cluster.mysql.reader_endpoint
}

output "ec2_public_ip" {
  value = aws_instance.loadgen.public_ip
}

output "ssh_command" {
  value = "ssh -i <key> ec2-user@${aws_instance.loadgen.public_ip}"
}

output "instance_ids" {
  value = {
    pg_writer    = aws_rds_cluster_instance.pg_writer.identifier
    pg_readers   = [for i in aws_rds_cluster_instance.pg_readers : i.identifier]
    mysql_writer = aws_rds_cluster_instance.mysql_writer.identifier
    mysql_readers = [for i in aws_rds_cluster_instance.mysql_readers : i.identifier]
  }
}
