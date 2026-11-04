variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "function_name" {
  description = "Name of the Lambda function"
  type        = string
  default     = "healthcheck"
}

variable "target_url" {
  description = "Optional URL the Lambda should check (empty = only report self status)"
  type        = string
  default     = ""
}

variable "schedule_expression" {
  description = "EventBridge schedule for periodic checks, e.g. rate(5 minutes). Empty disables it."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 14
}
