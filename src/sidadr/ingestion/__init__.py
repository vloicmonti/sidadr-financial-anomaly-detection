"""Module A — importation des fichiers CSV et XLSX."""

from sidadr.ingestion.errors import ImportRejected, MappingError
from sidadr.ingestion.importer import ImportedTable, ImportSession, Inspection, inspect_file
from sidadr.ingestion.mapping import ColumnMapping, Method, Proposal, propose_mapping

__all__ = [
    "ColumnMapping",
    "ImportRejected",
    "ImportSession",
    "ImportedTable",
    "Inspection",
    "MappingError",
    "Method",
    "Proposal",
    "inspect_file",
    "propose_mapping",
]
