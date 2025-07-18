from autogen_agentchat.messages import BaseChatMessage
from typing import List, Literal, Dict, Any

from autogen_core import Image
from autogen_core.models import (
    UserMessage,
)
from pydantic import BaseModel


class Document(BaseModel):
    """A document class for the multimodal message."""

    uri: str
    """The content of the document."""
    metadata: Dict[str, str] = {}
    type: Literal["Document"] = "Document"


class CustomMultiModalMessage(BaseChatMessage):
    """A custom multimodal message for more than text and image in comparison with the default agentchat's MultiModalMessage."""

    content: List[str | Image | Document]
    """The content of the message."""

    type: Literal["CustomMultiModalMessage"] = "CustomMultiModalMessage"

    def to_model_text(self, image_placeholder: str | None = "[image]") -> str:
        """Convert the content of the message to a string-only representation.
        If an image is present, it will be replaced with the image placeholder
        by default, otherwise it will be a base64 string when set to None.
        """
        text = ""
        for c in self.content:
            if isinstance(c, str):
                text += c
            elif isinstance(c, Image):
                if image_placeholder is not None:
                    text += f" {image_placeholder}"
                else:
                    text += f" {c.to_base64()}"
            elif isinstance(c, Document):
                text += f"{c.uri}"
            else:
                raise ValueError(f"Unsupported content type: {type(c)}")
        return text

    def to_text(self, iterm: bool = False) -> str:
        result: List[str] = []
        for c in self.content:
            if isinstance(c, str):
                result.append(c)
            elif isinstance(c, Image):
                if iterm:
                    # iTerm2 image rendering protocol: https://iterm2.com/documentation-images.html
                    image_data = c.to_base64()
                    result.append(f"\033]1337;File=inline=1:{image_data}\a\n")
                else:
                    result.append("<image>")
            elif isinstance(c, Document):
                result.append(c.uri)
            else:
                raise ValueError(f"Unsupported content type: {type(c)}")
        return "\n".join(result)

    def to_model_message(self) -> UserMessage:
        """Need more grained manners to convert the Document to a model message. It currently use the uri string as content
        which does not reveal the informative docuement content.
        to do:
        1. parse the uri and get the content as string 
        
        Returns:
            UserMessage: _description_
        """
        return UserMessage(content=[i if not isinstance(i, Document) else i.uri for i in self.content ],
                           source=self.source
                            )
    
    
__all__ = [
    "CustomMultiModalMessage",    
]    
