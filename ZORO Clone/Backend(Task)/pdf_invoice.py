import os
import io
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from brand import (
    STORE_NAME, STORE_TAGLINE, CODE_LABEL, SUPPORT_EMAIL,
    PRIMARY_COLOR, ACCENT_COLOR, format_eq_code, format_brand
)

# ─────────────────────────────────────────────
#  Register Authentic Montserrat TrueType Fonts
# ─────────────────────────────────────────────

_FONTS_REGISTERED = False

def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
    
    font_map = {
        'Montserrat': os.path.join(fonts_dir, 'Montserrat-Regular_static.ttf'),
        'Montserrat-Medium': os.path.join(fonts_dir, 'Montserrat-Medium.ttf'),
        'Montserrat-SemiBold': os.path.join(fonts_dir, 'Montserrat-SemiBold.ttf'),
        'Montserrat-Bold': os.path.join(fonts_dir, 'Montserrat-Bold.ttf'),
        'Montserrat-ExtraBold': os.path.join(fonts_dir, 'Montserrat-ExtraBold.ttf'),
    }
    
    for name, path in font_map.items():
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
            except Exception as e:
                pass
    _FONTS_REGISTERED = True


def generate_invoice_pdf(order) -> bytes:
    """
    Generate an official, professional vector PDF Commercial Tax Invoice
    with embedded authentic Montserrat TrueType typography matching
    official enterprise specifications.
    """
    _register_fonts()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Brand Palette
    teal_hex = PRIMARY_COLOR or "#19454B"
    rust_hex = ACCENT_COLOR or "#964C14"
    c_teal = colors.HexColor(teal_hex)
    c_rust = colors.HexColor(rust_hex)
    c_dark = colors.HexColor("#112629")
    c_gray = colors.HexColor("#586B6E")
    c_light_bg = colors.HexColor("#D1DDE0")
    c_border = colors.HexColor("#CBD5E0")

    # Typography Styles using Montserrat
    font_reg = 'Montserrat' if 'Montserrat' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'
    font_med = 'Montserrat-Medium' if 'Montserrat-Medium' in pdfmetrics.getRegisteredFontNames() else font_reg
    font_bold = 'Montserrat-Bold' if 'Montserrat-Bold' in pdfmetrics.getRegisteredFontNames() else 'Helvetica-Bold'
    font_xbold = 'Montserrat-ExtraBold' if 'Montserrat-ExtraBold' in pdfmetrics.getRegisteredFontNames() else font_bold

    style_logo = ParagraphStyle(
        'InvoiceLogo',
        parent=styles['Normal'],
        fontName=font_xbold,
        fontSize=24,
        leading=26,
        textColor=c_teal
    )
    style_logo_sub = ParagraphStyle(
        'InvoiceLogoSub',
        parent=styles['Normal'],
        fontName=font_med,
        fontSize=8.5,
        leading=11,
        textColor=c_gray
    )
    style_pay_callout = ParagraphStyle(
        'PayCallout',
        parent=styles['Normal'],
        fontName=font_med,
        fontSize=8,
        leading=11,
        textColor=c_dark
    )
    style_title = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Normal'],
        fontName=font_xbold,
        fontSize=22,
        leading=26,
        textColor=c_dark
    )
    style_meta_label = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=9,
        leading=12,
        textColor=c_dark
    )
    style_meta_val = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName=font_reg,
        fontSize=8.5,
        leading=12,
        textColor=c_dark
    )
    style_th = ParagraphStyle(
        'TableHead',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=8.5,
        leading=10,
        textColor=c_dark
    )
    style_td = ParagraphStyle(
        'TableData',
        parent=styles['Normal'],
        fontName=font_reg,
        fontSize=8.5,
        leading=11.5,
        textColor=c_dark
    )
    style_td_bold = ParagraphStyle(
        'TableDataBold',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=8.5,
        leading=11.5,
        textColor=c_dark
    )
    style_td_code = ParagraphStyle(
        'TableDataCode',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=8.5,
        leading=11.5,
        textColor=c_teal
    )
    style_legal = ParagraphStyle(
        'LegalText',
        parent=styles['Normal'],
        fontName=font_reg,
        fontSize=7,
        leading=9.5,
        textColor=colors.HexColor("#718096")
    )
    style_legal_bold = ParagraphStyle(
        'LegalTextBold',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=7.5,
        leading=10.5,
        textColor=c_dark
    )

    story = []

    # 1. HEADER SECTION: Logo + Top-Right Callout
    header_left = [
        Paragraph(f'<b>EQUIP<font color="{rust_hex}">T</font></b>', style_logo),
        Paragraph('Tools, Parts and Supplies for Your Business™', style_logo_sub),
    ]
    callout_text = (
        '<b>Save time and pay online!</b> Sign in to your account at '
        '<u>www.equipt.dev/invoices</u> to pay your invoice.'
    )
    header_right = [
        Table(
            [[Paragraph(callout_text, style_pay_callout)]],
            colWidths=[200],
            style=TableStyle([
                ('BOX', (0,0), (-1,-1), 1.5, c_dark),
                ('BACKGROUND', (0,0), (-1,-1), colors.white),
                ('PADDING', (0,0), (-1,-1), 6),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ])
        )
    ]
    header_table = Table([[header_left, header_right]], colWidths=[330, 210])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    # 2. INVOICE TITLE
    story.append(Paragraph('Invoice', style_title))
    story.append(Spacer(1, 10))

    # Order details setup
    created_dt = order.created_at if order.created_at else datetime.now()
    due_dt = order.net30_due_date if order.net30_due_date else (created_dt + timedelta(days=30))
    inv_no = order.invoice_number or f"INV-{datetime.now().year}-{order.order_id:06d}"
    user = getattr(order, "user", None)
    customer_no = f"EQCUST{user.user_id}" if user else "EQCUST1"
    order_no = getattr(order, "order_number", None) or f"EQ{order.order_id}"

    # 3. 3-COLUMN METADATA GRID
    col1_content = [
        Paragraph(f'<b>Invoice #:</b> {inv_no}', style_meta_val),
        Paragraph(f'<b>Invoice Date:</b> {created_dt.strftime("%m/%d/%Y")}', style_meta_val),
        Paragraph(f'<b>Amount Due:</b> ${float(order.total or 0):.2f}', style_meta_val),
        Paragraph(f'<b>Due Date:</b> {due_dt.strftime("%m/%d/%Y")}', style_meta_val),
    ]

    bill_company = user.company if (user and user.company) else "Commercial Account"
    col2_content = [
        Paragraph('<b>Bill To:</b>', style_meta_label),
        Paragraph(f'{order.bill_first_name or order.ship_first_name} {order.bill_last_name or order.ship_last_name}', style_meta_val),
        Paragraph(f'{bill_company}', style_meta_val),
        Paragraph(f'{order.bill_address1 or order.ship_address1}', style_meta_val),
        Paragraph(f'{order.bill_phone or order.ship_phone or "(555) 123-4567"}', style_meta_val),
        Paragraph(f'{order.bill_city or order.ship_city} {order.bill_state or order.ship_state} {order.bill_zip or order.ship_zip}', style_meta_val),
        Paragraph('United States', style_meta_val),
    ]

    col3_content = [
        Paragraph('<b>Ship To:</b>', style_meta_label),
        Paragraph(f'{order.ship_first_name} {order.ship_last_name}', style_meta_val),
        Paragraph(f'{order.ship_address1}', style_meta_val),
        Paragraph(f'{order.ship_phone or "(555) 123-4567"}', style_meta_val),
        Paragraph(f'{order.ship_city} {order.ship_state} {order.ship_zip}', style_meta_val),
        Paragraph('United States', style_meta_val),
    ]

    meta_table = Table([[col1_content, col2_content, col3_content]], colWidths=[180, 180, 180])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    # 4. SUMMARY STRIP TABLE
    strip_header = [
        Paragraph('PO#', style_th),
        Paragraph('Terms', style_th),
        Paragraph('Order #', style_th),
        Paragraph('Customer', style_th),
        Paragraph('Shipping Method', style_th),
        Paragraph('Ship Date', style_th),
    ]
    strip_data = [
        Paragraph(str(getattr(order, 'po_number', None) or 'N/A'), style_td_bold),
        Paragraph('Net 30' if order.payment_method == 'net30' else 'Credit Card', style_td),
        Paragraph(order_no, style_td_bold),
        Paragraph(customer_no, style_td),
        Paragraph(order.carrier or 'FedEx Ground', style_td),
        Paragraph(created_dt.strftime("%m/%d/%Y"), style_td),
    ]
    strip_table = Table([strip_header, strip_data], colWidths=[80, 80, 95, 95, 110, 80])
    strip_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_light_bg),
        ('TEXTCOLOR', (0,0), (-1,0), c_dark),
        ('PADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, c_border),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(strip_table)
    story.append(Spacer(1, 14))

    # 5. ITEMIZED ITEMS TABLE
    items_header = [
        Paragraph('EQ #', style_th),
        Paragraph('Item Description', style_th),
        Paragraph('Qty', style_th),
        Paragraph('Unit Price', style_th),
        Paragraph('Total', style_th),
    ]
    items_rows = [items_header]
    for i in (order.items or []):
        uprice = float(i.unit_price or 0)
        qty = int(i.quantity or 1)
        tot = uprice * qty
        items_rows.append([
            Paragraph(format_eq_code(i.zoro_code), style_td_code),
            Paragraph(i.title, style_td),
            Paragraph(str(qty), style_td),
            Paragraph(f"${uprice:.2f}", style_td),
            Paragraph(f"<b>${tot:.2f}</b>", style_td_bold),
        ])

    items_table = Table(items_rows, colWidths=[90, 270, 45, 65, 70])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_light_bg),
        ('PADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, c_border),
        ('ALIGN', (2,0), (2,-1), 'CENTER'),
        ('ALIGN', (3,0), (4,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(items_table)

    # Tracking row
    tracking_str = order.tracking_number or 'In Transit / Preparing Shipment'
    tracking_table = Table([[Paragraph(f'<b>Tracking Number(s):</b> {tracking_str}', style_td)]], colWidths=[540])
    tracking_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFB")),
        ('BOX', (0,0), (-1,-1), 0.5, c_border),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(tracking_table)
    story.append(Spacer(1, 14))

    # 6. TOTALS & LEGAL DISCLAIMERS
    legal_content = [
        Paragraph(f'<b>Pay with ACH and more at <u>www.equipt.dev/invoices</u>.</b><br/>For questions about this invoice please call (855) 666-4649.', style_legal_bold),
        Spacer(1, 6),
        Paragraph(
            f'This purchase is governed exclusively by the {STORE_NAME} Terms & Conditions in effect at the time of the order. '
            'Product return instructions are available at www.equipt.dev/returns.',
            style_legal
        ),
        Spacer(1, 4),
        Paragraph(
            'These items are sold for domestic consumption in the United States. If exported, purchaser assumes full responsibility for compliance with U.S. export controls.',
            style_legal
        ),
    ]

    subtotal = float(order.subtotal or 0)
    shipping = float(order.shipping_cost or 0)
    tax = float(order.tax or 0)
    total = float(order.total or 0)

    totals_data = [
        [Paragraph('Subtotal', style_td), Paragraph(f'${subtotal:.2f}', style_td_bold)],
        [Paragraph('Shipping Cost', style_td), Paragraph(f'${shipping:.2f}', style_td_bold)],
        [Paragraph('Total Tax (%)', style_td), Paragraph(f'${tax:.2f}', style_td_bold)],
        [Paragraph('Total Amount', style_td_bold), Paragraph(f'${total:.2f}', style_td_bold)],
        [Paragraph('Amount Paid', style_td), Paragraph('$0.00', style_td_bold)],
        [Paragraph('<b>Total Due</b>', ParagraphStyle('TotG', parent=style_td_bold, fontSize=11, fontName=font_bold, textColor=c_teal)),
         Paragraph(f'<b>${total:.2f}</b>', ParagraphStyle('TotGVal', parent=style_td_bold, fontSize=11, fontName=font_bold, textColor=c_teal))],
    ]
    totals_table = Table(totals_data, colWidths=[100, 90])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('PADDING', (0,0), (-1,-1), 3),
        ('LINEBELOW', (0,-2), (-1,-2), 0.5, c_border),
        ('LINEABOVE', (0,-1), (-1,-1), 1.5, c_dark),
    ]))

    bottom_table = Table([[legal_content, totals_table]], colWidths=[340, 200])
    bottom_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(bottom_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
