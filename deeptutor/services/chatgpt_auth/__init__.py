"""Official Sign in with ChatGPT for locally hosted DeepTutor installations."""

from .service import ChatGPTAuthError, ChatGPTAuthService, get_chatgpt_auth_service

__all__ = ["ChatGPTAuthError", "ChatGPTAuthService", "get_chatgpt_auth_service"]
