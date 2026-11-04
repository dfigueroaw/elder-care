terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Package the handler into a zip on every plan/apply
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/build/healthcheck.zip"
}

# IAM role the Lambda runs as
data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "basic_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "healthcheck" {
  function_name    = var.function_name
  role             = aws_iam_role.lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      SERVICE_NAME = var.function_name
      TARGET_URL   = var.target_url
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.basic_logs,
    aws_cloudwatch_log_group.lambda,
  ]
}

# Public HTTPS endpoint so the healthcheck can be hit with curl / a monitor
resource "aws_lambda_function_url" "healthcheck" {
  function_name      = aws_lambda_function.healthcheck.function_name
  authorization_type = "NONE"
}

# Optional: run the check on a schedule (set schedule_expression = "" to disable)
resource "aws_cloudwatch_event_rule" "schedule" {
  count               = var.schedule_expression == "" ? 0 : 1
  name                = "${var.function_name}-schedule"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "schedule" {
  count = var.schedule_expression == "" ? 0 : 1
  rule  = aws_cloudwatch_event_rule.schedule[0].name
  arn   = aws_lambda_function.healthcheck.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  count         = var.schedule_expression == "" ? 0 : 1
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.healthcheck.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[0].arn
}
