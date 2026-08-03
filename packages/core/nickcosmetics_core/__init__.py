"""
nickcosmetics_core
===================

Framework-agnostic domain and application layer for the Nick Cosmetics
platform. This package has zero dependencies on FastAPI, SQLAlchemy,
Django, or any other infrastructure concern — it is imported by both
the cloud API (apps/api) and the offline desktop app (apps/desktop_pos)
so that business rules are defined exactly once.

Layout:
    domain/         Entities, value objects, domain exceptions
    application/    Use-case services that orchestrate domain logic
    interfaces/     Abstract repository/gateway contracts implemented
                     by infrastructure code in each app
"""

__version__ = "0.1.0"
