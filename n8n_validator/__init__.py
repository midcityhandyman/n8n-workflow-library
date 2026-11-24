"""Offline validator for n8n workflow JSON exports.

Four families of checks:
  STRUCT  the file is a well formed n8n workflow export
  ERR     every workflow has retries, an error branch and a dead letter path
  CRED    no credentials or secrets are embedded in the file
  SMS     customer facing texts carry an opt-out line and check the opt-out store first
"""

from .checks import Finding, validate_file, validate_workflow

__all__ = ["Finding", "validate_file", "validate_workflow"]
__version__ = "1.1.0"
