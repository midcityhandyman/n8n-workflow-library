"""Offline validator for n8n workflow JSON exports.

Three families of checks:
  STRUCT  the file is a well formed n8n workflow export
  ERR     every workflow has retries, an error branch and a dead letter path
  CRED    no credentials or secrets are embedded in the file
"""

from .checks import Finding, validate_file, validate_workflow

__all__ = ["Finding", "validate_file", "validate_workflow"]
__version__ = "1.0.0"
