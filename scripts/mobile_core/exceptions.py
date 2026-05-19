class MobileDriverException(Exception):
    """Base exception for mobile driver."""
    pass

class RiskControlTriggered(MobileDriverException):
    """Raised when a slider or security verification is detected."""
    pass

class ElementNotFoundError(MobileDriverException):
    """Raised when an expected UI element is not found on screen."""
    pass

class PopupIntercepted(MobileDriverException):
    """Raised when a system or app popup intercepts the flow."""
    pass

class OCRServiceError(MobileDriverException):
    """Raised when the OCR microservice fails to respond."""
    pass
