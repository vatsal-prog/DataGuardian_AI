"""Errors that should be shown to the person running a pipeline."""


class PipelineError(Exception):
    """The source or request cannot be processed."""
