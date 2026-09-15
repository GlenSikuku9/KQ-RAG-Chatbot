from pydantic import BaseModel, Field


class KnowledgeBaseDocument(BaseModel):
    """Text extracted from one source file in the Kenya Airways knowledge base."""

    source: str = Field(..., description="Original file name, for example FAQs.docx.")
    file_type: str = Field(..., description="Original file extension, for example docx or pdf.")
    category: str = Field(..., description="Knowledge-base category inferred from the file name.")
    text: str = Field(..., description="Clean text extracted from the document.")
