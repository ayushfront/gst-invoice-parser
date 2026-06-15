from typing import Optional
from pydantic import BaseModel


class SellerBuyer(BaseModel):
    name: Optional[str] = None
    gstin: Optional[str] = None
    address: Optional[str] = None
    state_code: Optional[str] = None


class LineItem(BaseModel):
    description: Optional[str] = None
    hsn_sac_code: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    taxable_amount: Optional[float] = None
    cgst_rate: Optional[float] = None
    sgst_rate: Optional[float] = None
    igst_rate: Optional[float] = None
    cgst_amount: Optional[float] = None
    sgst_amount: Optional[float] = None
    igst_amount: Optional[float] = None
    total_amount: Optional[float] = None


class TaxSummary(BaseModel):
    subtotal: Optional[float] = None
    total_cgst: Optional[float] = None
    total_sgst: Optional[float] = None
    total_igst: Optional[float] = None
    total_cess: Optional[float] = None
    round_off: Optional[float] = None
    grand_total: Optional[float] = None
    amount_in_words: Optional[str] = None


class Payment(BaseModel):
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    due_date: Optional[str] = None


class Meta(BaseModel):
    confidence_score: Optional[float] = None
    extraction_time_ms: Optional[int] = None
    pages_processed: Optional[int] = None
    currency: Optional[str] = "INR"
    truncated: Optional[bool] = None
    warning: Optional[str] = None


class InvoiceData(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    invoice_type: Optional[str] = None
    seller: Optional[SellerBuyer] = None
    buyer: Optional[SellerBuyer] = None
    line_items: Optional[list[LineItem]] = None
    tax_summary: Optional[TaxSummary] = None
    payment: Optional[Payment] = None
    meta: Optional[Meta] = None


class SuccessResponse(BaseModel):
    success: bool = True
    data: InvoiceData


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: str = ""


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
