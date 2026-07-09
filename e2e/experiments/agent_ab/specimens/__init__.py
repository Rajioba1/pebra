"""Language specimens for the PEBRA agent A/B experiment.

Each subpackage (``csharp``, ``javascript``, ...) holds one language's corpus (tasks/oracles/patches)
that plugs into the shared ``agent_ab`` framework. The import-path identifiers are Python-safe
(``csharp``, not "C# experiment"); the human-readable experiment titles live in each subpackage's
docstring / README.
"""
