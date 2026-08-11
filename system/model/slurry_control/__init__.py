"""Compatibility namespace for the slurry-control core.

The new project keeps the copied industrial shell under ``system.model`` while
placing the two slurry core modules under ``system.model.map_control``.  The
core was originally developed with imports rooted at
``system.model.slurry_control``.  Child compatibility packages redirect those
imports to the real modules without duplicating model code.
"""
