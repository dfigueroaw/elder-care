output "function_name" {
  value = aws_lambda_function.healthcheck.function_name
}

output "function_url" {
  description = "Public HTTPS URL for the healthcheck"
  value       = aws_lambda_function_url.healthcheck.function_url
}
