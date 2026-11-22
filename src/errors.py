class PipelineError(Exception):
    """Erro esperado, com mensagem segura para o operador e os logs."""


class DuplicateError(PipelineError):
    """Tema ou roteiro já registrado no workspace."""
