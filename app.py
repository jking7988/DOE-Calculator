# app.py
import math, uuid
from datetime import datetime

import dash
from dash import html, dcc, Input, Output, State, dash_table, callback_context
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
from dash.dcc import send_data_frame, send_bytes
import urllib.parse as _url

# PDF (ReportLab)
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from core import pricebook

# ---- Version label (UI + PDF) ----
# bump2version updates APP_VERSION automatically; we also read VERSION as source of truth.
APP_VERSION = "0.2.0"
def _read_version_fallback():
    try:
        with open("VERSION", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return APP_VERSION

# ---- Load pricebook at startup (root Excel or env path) ----
pricebook.ensure_loaded()
PRICEBOOK_SOURCE = pricebook.get_source()

# ---- Constants / SKUs ----
FABRIC_SKU_14G = "silt-fence-14g"
FABRIC_SKU_125G = "silt-fence-12g5"
POST_SKU_T_POST_4FT = "t-post-4ft"
POST_SKU_TXDOT_T_POST_4FT = "tx-dot-t-post-4-ft"
POST_SKU_T_POST_6FT = "t-post-6ft"
FABRIC_SKU_ORANGE_LIGHT = "orange-fence-light-duty"
FABRIC_SKU_ORANGE_HEAVY = "orange-fence-heavy-duty"
CAP_SKU_OSHA = "cap-osha"
CAP_SKU_PLASTIC = "cap-plastic"

SALES_TAX = 0.0825
PROD_LF_PER_DAY = 2500

def required_footage(total_lf: float, waste_pct: float) -> float:
    return max(0.0, float(total_lf or 0)) * (1.0 + max(0.0, float(waste_pct or 0))/100.0)

def posts_needed(required_ft: float, spacing_ft: int) -> int:
    rf=max(0.0, float(required_ft or 0)); sp=max(1, int(spacing_ft or 1))
    return int(math.ceil(rf/sp)) + (1 if rf>0 else 0)

def rolls_needed(required_ft: float, roll_len: int=100) -> int:
    rf=max(0.0,float(required_ft or 0)); rl=max(1,int(roll_len or 100))
    return int(math.ceil(rf/rl)) if rf>0 else 0

def get_labor_per_day()->float: return 554.34
def fuel_cost(days:int, any_work:bool)->float: return (65.0*max(0,int(days or 0))) if any_work else 0.0

def materials_breakdown(required_ft: float, cost_per_lf: float, posts_count: int, post_unit_cost: float, tax_rate: float=SALES_TAX):
    fabric_cost = max(0.0,required_ft)*max(0.0,cost_per_lf)
    hardware_cost = max(0,posts_count)*max(0.0,post_unit_cost)
    subtotal = fabric_cost + hardware_cost
    tax = subtotal*tax_rate
    return fabric_cost, hardware_cost, subtotal, tax

# ---- Theme (Bootstrap + tiny CSS) ----
external_stylesheets = [dbc.themes.BOOTSTRAP]
app = dash.Dash(__name__, external_stylesheets=external_stylesheets)
app.title = f"Double Oak Fencing Estimator (Dash) – v{_read_version_fallback()}"

app.index_string = """
<!DOCTYPE html>
<html>
<head>
  {%metas%}
  <title>{%title%}</title>
  {%favicon%}
  {%css%}
  <style>
    body { background: #f5fbf6; }
    .do-card { border: 2px solid #2e6d33; border-radius: 12px; background:#fff; box-shadow: 0 2px 10px rgba(0,0,0,.06); }
    .do-title { font-weight:800; color:#0f172a; border-bottom:2px dashed #b2deb5; padding-bottom:6px; margin-bottom:12px; }
    .status-badge { border:2px solid #8fd095; border-radius:10px; padding:10px 24px; font-weight:800; display:inline-block; background:linear-gradient(90deg,#2e6d33,#17381b); color:#fff; }
  </style>
</head>
<body>
  {%app_entry%}
  <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
"""

# ---- Sidebar controls ----
sidebar = dbc.Offcanvas(
    id="sidebar",
    title="Fencing Options",
    is_open=True,
    placement="start",
    scrollable=True,
    style={"width":"360px","background":"#eaf6ec","borderRight":"3px solid #2e6d33"},
    children=[
        dbc.Label("Project Title"), dcc.Input(id="project_name", type="text", placeholder="Lakeside Retail – Phase 2", style={"width":"100%"}),
        dbc.Label("Customer Name"), dcc.Input(id="company_name", type="text", placeholder="ACME Builders", style={"width":"100%"}),
        dbc.Label("Address"), dcc.Input(id="project_address", type="text", placeholder="1234 Main St, Austin, TX", style={"width":"100%"}),

        html.Hr(),
        dbc.Label("Fencing Material"),
        dcc.Dropdown(id="fence_category", options=[
            {"label":"Silt Fence","value":"Silt Fence"},
            {"label":"Plastic Orange Fence","value":"Plastic Orange Fence"},
        ], value="Silt Fence", clearable=False),

        dbc.Label("Total Job Footage (ft)"),
        dcc.Input(id="total_lf", type="number", min=0, step=1, value=1000, style={"width":"100%"}),

        dbc.Label("Waste %"),
        dcc.Slider(id="waste_pct", min=0, max=10, step=1, value=2, marks=None, tooltip={"placement":"bottom","always_visible":True}),

        html.Div(id="silt_opts", children=[
            dbc.Label("Silt Fence Gauge"),
            dcc.Dropdown(id="sf_gauge", options=[{"label":"14 Gauge","value":"14 Gauge"}, {"label":"12.5 Gauge","value":"12.5 Gauge"}], value="14 Gauge", clearable=False),
            dbc.Label("T-Post Spacing (ft)"),
            dcc.Dropdown(id="sf_spacing", options=[3,4,6,8,10], value=8, clearable=False),
            dbc.Label("Final Price / LF"),
            dcc.Input(id="sf_price_lf", type="number", min=0, step=0.01, value=2.50, style={"width":"100%"}),
            dbc.Checklist(options=[{"label":"Add safety caps","value":"caps"}], value=[], id="sf_caps"),
            dcc.Dropdown(id="sf_cap_type", options=[
                {"label":"OSHA-Approved ($3.90)","value":"OSHA"},
                {"label":"Regular Plastic Cap ($1.05)","value":"PLASTIC"}
            ], value="OSHA", clearable=False),
            dbc.Checklist(options=[{"label":"Add fence removal pricing","value":"removal"}], value=[], id="sf_removal"),
            dbc.Checklist(options=[{"label":"Remove sales tax from customer printout","value":"remove_tax"}], value=[], id="sf_remove_tax"),
        ]),

        html.Div(id="orange_opts", children=[
            dbc.Label("Orange Fence Duty"),
            dcc.Dropdown(id="orange_duty", options=[{"label":"Light Duty","value":"Light Duty"},{"label":"Heavy Duty","value":"Heavy Duty"}], value="Light Duty", clearable=False),
            dbc.Label("T-Post Spacing (ft)"),
            dcc.Dropdown(id="orange_spacing", options=[3,4,6,8,10], value=10, clearable=False),
            dbc.Label("Final Price / LF"),
            dcc.Input(id="orange_price_lf", type="number", min=0, step=0.01, value=2.50, style={"width":"100%"}),
            dbc.Checklist(options=[{"label":"Add fence removal pricing","value":"removal"}], value=[], id="orange_removal"),
            dbc.Checklist(options=[{"label":"Remove sales tax from customer printout","value":"remove_tax"}], value=[], id="orange_remove_tax"),
        ])
    ],
)

# ---- Main content layout ----
cards = dbc.Row([
    dbc.Col(dbc.Card(dbc.CardBody([html.H4("Cost Summary", className="do-title"), html.Div(id="cost_summary")] ), className="do-card"), md=4),
    dbc.Col(dbc.Card(dbc.CardBody([html.H4("Total Costs Breakdown", className="do-title"), html.Div(id="total_costs")]), className="do-card"), md=4),
    dbc.Col(dbc.Card(dbc.CardBody([html.H4("Material Cost Breakdown", className="do-title"), html.Div(id="material_costs")]), className="do-card"), md=4),
], className="g-4")

gauge = dbc.Row([dbc.Col([html.Div(id="profit_status"), dcc.Graph(id="profit_gauge", config={"displayModeBar": False}, style={"height":"120px"})], md=12)])

table_section = dbc.Row([
    dbc.Col([
        html.H4("📑 Customer Printout Preview", className="do-title"),
        dash_table.DataTable(
            id="preview_table",
            columns=[
                {"name":"Qty","id":"Qty","type":"numeric"},
                {"name":"Item","id":"Item"},
                {"name":"Unit","id":"Unit"},
                {"name":"Price Each","id":"Price Each","type":"numeric","format":dash_table.FormatTemplate.money(2)},
                {"name":"Line Total","id":"Line Total","type":"numeric","format":dash_table.FormatTemplate.money(2)},
            ],
            data=[], editable=True, row_deletable=True,
            style_table={"overflowX":"auto"},
            style_cell={"fontFamily":"Inter, system-ui, -apple-system, Segoe UI, Roboto","fontSize":"15px","padding":"8px"},
            style_header={"fontWeight":"700","backgroundColor":"#2e6d33","color":"#fff"},
        ),
        html.Div(id="totals_right", style={"textAlign":"right","marginTop":"10px","fontWeight":"700"})
    ], md=12)
])

# ---- Material takeoff + actions ----
materials_section = dbc.Row([
    dbc.Col([
        html.H4("📦 Material Takeoff", className="do-title"),
        dash_table.DataTable(
            id="materials_table",
            columns=[
                {"name":"Qty","id":"Qty","type":"numeric"},
                {"name":"Item","id":"Item"},
                {"name":"Unit","id":"Unit"},
                {"name":"Notes","id":"Notes"},
            ],
            data=[], editable=False,
            style_table={"overflowX":"auto"},
            style_cell={"fontFamily":"Inter, system-ui, -apple-system, Segoe UI, Roboto","fontSize":"15px","padding":"8px"},
            style_header={"fontWeight":"700","backgroundColor":"#2e6d33","color":"#fff"},
        ),
        html.Div(className="d-flex gap-2", children=[
            dcc.Download(id="download_customer_csv"),
            dcc.Download(id="download_materials_csv"),
            dcc.Download(id="download_pdf"),
            dbc.Button("⬇️ Download Customer CSV", id="btn_download_customer", color="success", className="mt-2"),
            dbc.Button("⬇️ Download Materials CSV", id="btn_download_materials", color="secondary", className="mt-2"),
            dbc.Button("📄 Download Quote PDF", id="btn_download_pdf", color="dark", className="mt-2"),
            html.A(dbc.Button("✉️ Email Quote", id="btn_email", color="info", className="mt-2"),
                   id="mailto_link", href="#", target="_blank", style={"textDecoration":"none"})
        ])
    ], md=12)
])

# ---- Layout ----
app.layout = dbc.Container([
    dcc.Store(id="lines_store", data=[]),
    sidebar,
    html.Br(),
    html.Div(dbc.Alert(f"Pricebook source: {PRICEBOOK_SOURCE}", color="success")),
    cards,
    html.Br(),
    gauge,
    html.Hr(),
    table_section,
    html.Br(), materials_section,

    # Sticky footer version badge
html.Footer(
    dbc.Badge(f"Double Oak Estimator – v{_read_version_fallback()}",
              color="secondary", pill=True, class_name="shadow-sm"),
    style={
        "position": "fixed",
        "bottom": "10px",
        "right": "12px",
        "zIndex": 9999,
        "background": "transparent"
    }
)
], fluid=True)

# ---- Callbacks ----
@app.callback(
    Output("silt_opts","style"),
    Output("orange_opts","style"),
    Input("fence_category","value")
)
def toggle_category(cat):
    if cat == "Silt Fence":
        return ({}, {"display":"none"})
    return ({"display":"none"}, {})

@app.callback(
    Output("cost_summary","children"),
    Output("total_costs","children"),
    Output("material_costs","children"),
    Output("profit_status","children"),
    Output("profit_gauge","figure"),
    Output("preview_table","data"),
    Output("totals_right","children"),
    Output("materials_table","data"),
    Input("preview_table","data"),
    Input("fence_category","value"),
    Input("total_lf","value"),
    Input("waste_pct","value"),
    Input("sf_gauge","value"),
    Input("sf_spacing","value"),
    Input("sf_price_lf","value"),
    Input("sf_caps","value"),
    Input("sf_cap_type","value"),
    Input("sf_removal","value"),
    Input("sf_remove_tax","value"),
    Input("orange_duty","value"),
    Input("orange_spacing","value"),
    Input("orange_price_lf","value"),
    Input("orange_removal","value"),
    Input("orange_remove_tax","value"),
    Input("project_name","value"),
    Input("company_name","value"),
    Input("project_address","value"),
)
def compute(preview_data, cat, total_lf, waste_pct, sf_gauge, sf_spacing, sf_price_lf, sf_caps, sf_cap_type, sf_removal, sf_remove_tax,
            orange_duty, orange_spacing, orange_price_lf, orange_removal, orange_remove_tax,
            project_name, company_name, project_address):

    # inputs
    total_lf = int(total_lf or 0)
    waste_pct = int(waste_pct or 0)
    remove_tax_flag = False
    include_caps=False; cap_type=None
    post_spacing = 8
    final_price_per_lf = 2.50

    if cat=="Silt Fence":
        post_spacing = int(sf_spacing or 8)
        final_price_per_lf = float(sf_price_lf or 2.50)
        include_caps = ("caps" in (sf_caps or []))
        cap_type = sf_cap_type
        remove_tax_flag = ("remove_tax" in (sf_remove_tax or []))
        if (sf_gauge or "").startswith("14"):
            fabric_sku, fabric_default = FABRIC_SKU_14G, 0.32
            post_sku, post_default = POST_SKU_T_POST_4FT, 1.80
        else:
            fabric_sku, fabric_default = FABRIC_SKU_125G, 0.38
            post_sku, post_default = POST_SKU_TXDOT_T_POST_4FT, 2.15
        removal_selected = ("removal" in (sf_removal or []))
    else:
        post_spacing = int(orange_spacing or 10)
        final_price_per_lf = float(orange_price_lf or 2.50)
        remove_tax_flag = ("remove_tax" in (orange_remove_tax or []))
        if (orange_duty or "").startswith("Light"):
            fabric_sku, fabric_default = FABRIC_SKU_ORANGE_LIGHT, 0.30
        else:
            fabric_sku, fabric_default = FABRIC_SKU_ORANGE_HEAVY, 0.45
        post_sku, post_default = POST_SKU_T_POST_6FT, 2.25
        removal_selected = ("removal" in (orange_removal or []))

    # prices
    cost_per_lf   = pricebook.get_price(fabric_sku, fabric_default) or fabric_default
    post_unit_cost= pricebook.get_price(post_sku, post_default) or post_default
    caps_unit_cost= 0.0
    if cat=="Silt Fence" and include_caps and cap_type:
        if cap_type=="OSHA":
            caps_unit_cost = pricebook.get_price(CAP_SKU_OSHA, 3.90) or 3.90
        else:
            caps_unit_cost = pricebook.get_price(CAP_SKU_PLASTIC, 1.05) or 1.05

    # calcs
    req_ft = required_footage(total_lf, waste_pct)
    posts = posts_needed(req_ft, post_spacing)
    rolls = rolls_needed(req_ft)
    caps_qty = posts if (cat=="Silt Fence" and include_caps and cap_type) else 0
    caps_cost = caps_qty * caps_unit_cost

    fabric_cost, hardware_cost, mat_sub, tax = materials_breakdown(req_ft, cost_per_lf, posts, post_unit_cost, SALES_TAX)
    mat_sub_all = mat_sub + caps_cost
    tax_all = tax + caps_cost*SALES_TAX

    project_days = (req_ft/PROD_LF_PER_DAY) if req_ft>0 else 0.0
    labor_cost = project_days*get_labor_per_day()
    billing_days = math.ceil(project_days) if req_ft>0 else 0
    fuel = fuel_cost(billing_days, any_work=req_ft>0)

    # removal pricing helper
    def _calc_removal(qty_lf, sell_per_lf):
        if qty_lf<=0: return 0.0,0.0
        unit = sell_per_lf*0.40
        unit = max(unit, 1.15) if qty_lf<800 else max(unit, 0.90)
        total = unit*qty_lf
        if total < 800: total=800.0; unit = total/qty_lf
        return unit, total

    # Customer-facing revenue uses quoted footage, not required footage with waste
    customer_qty = int(total_lf or 0)
    removal_unit_lf, _ = _calc_removal(customer_qty, final_price_per_lf) if removal_selected else (0.0, 0.0)
    sell_total_main = final_price_per_lf*customer_qty if customer_qty>0 else 0.0
    caps_revenue = caps_unit_cost*caps_qty if caps_qty>0 else 0.0
    removal_revenue = (removal_unit_lf*customer_qty) if (removal_selected and customer_qty>0) else 0.0
    customer_subtotal_display = sell_total_main + caps_revenue + removal_revenue
    customer_sales_tax = 0.0 if remove_tax_flag else customer_subtotal_display*SALES_TAX
    customer_total = customer_subtotal_display + customer_sales_tax

    internal_total_cost = mat_sub_all + tax_all + labor_cost + fuel
    subtotal_for_margin = sell_total_main + caps_revenue  # keep margin on main scope/caps
    gross_profit = subtotal_for_margin - internal_total_cost
    profit_margin = (gross_profit/subtotal_for_margin) if subtotal_for_margin>0 else 0.0

    # ---- Panels (HTML) ----
    cs = dbc.Table([
        html.Tbody([
            html.Tr([html.Td("Subtotal (excl. sales tax)"), html.Td(f"${customer_subtotal_display:,.2f}", style={"textAlign":"right"})]),
            html.Tr([html.Td(f"Sales Tax ({0 if remove_tax_flag else SALES_TAX*100:.2f}%)"), html.Td(f"${customer_sales_tax:,.2f}", style={"textAlign":"right"})]),
            html.Tr([html.Td(html.Strong("Customer Total")), html.Td(html.Strong(f"${customer_total:,.2f}"), style={"textAlign":"right"})]),
            html.Tr([html.Td("Gross Profit"), html.Td(f"${gross_profit:,.2f}", style={"textAlign":"right"})]),
        ])
    ], bordered=False, striped=True, hover=False, size="sm")

    tc = dbc.Table([
        html.Tbody([
            html.Tr([html.Td("Total Material Cost"), html.Td(f"${mat_sub_all:,.2f}", style={"textAlign":"right"})]),
            html.Tr([html.Td("Labor Cost"), html.Td(f"${labor_cost:,.2f}", style={"textAlign":"right"})]),
            html.Tr([html.Td("Fuel"), html.Td(f"${fuel:,.2f}", style={"textAlign":"right"})]),
            html.Tr([html.Td("Final Price / LF (sell)"), html.Td(f"${final_price_per_lf:,.2f}", style={"textAlign":"right"})]),
        ])
    ], bordered=False, striped=True, hover=False, size="sm")

    mc_rows = [
        html.Tr([html.Td(("Fabric (Silt Fence)" if cat=="Silt Fence" else f"Plastic Orange Fence")), html.Td(f"${fabric_cost:,.2f}", style={"textAlign":"right"})]),
        html.Tr([html.Td("T-Post Cost"), html.Td(f"${hardware_cost:,.2f}", style={"textAlign":"right"})]),
    ]
    if caps_qty>0:
        mc_rows.append(html.Tr([html.Td("Safety Caps"), html.Td(f"${caps_cost:,.2f}", style={"textAlign":"right"})]))
    mc_rows.extend([
        html.Tr([html.Td("Total Material Cost"), html.Td(f"${mat_sub_all:,.2f}", style={"textAlign":"right"})]),
        html.Tr([html.Td("Total Material Cost / LF"), html.Td(f"${(mat_sub_all/req_ft) if req_ft>0 else 0.0:,.2f}", style={"textAlign":"right"})]),
    ])
    mc = dbc.Table(html.Tbody(mc_rows), bordered=False, striped=True, hover=False, size="sm")

    # ---- Profit badge + gauge ----
    badge = html.Span(f"PROFIT {'GOOD' if profit_margin>=0.30 else 'CHECK PROFIT'}   {profit_margin*100:.1f}%", className="status-badge")

    m_val = profit_margin*100.0
    target = 30.0
    xmax = max(60.0, target+10.0, m_val+10.0)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[m_val], y=[""], orientation="h", marker=dict(color="#44a04c"), hoverinfo="skip", showlegend=False))
    fig.update_layout(
        xaxis=dict(range=[0,xmax], showgrid=False, visible=True, title="Profit %"),
        yaxis=dict(showgrid=False, visible=False),
        margin=dict(l=10,r=10,t=10,b=10),
        height=120
    )
    fig.add_vline(x=target, line_dash="dash", line_color="#ff9d00")

    # ---- Customer table (preserve user edits if table triggered the callback) ----
    lines = []
    if customer_qty>0:
        item_name = ("14 Gauge Silt Fence" if (cat=="Silt Fence" and (sf_gauge or "").startswith("14")) else
                     ("12.5 Gauge Silt Fence" if cat=="Silt Fence" else f"Plastic Orange Fence"))
        lines.append({"_id":str(uuid.uuid4()), "Qty":customer_qty, "Item":item_name, "Unit":"LF",
                      "Price Each":float(final_price_per_lf), "Line Total":float(final_price_per_lf)*customer_qty})
    if caps_qty>0:
        lines.append({"_id":str(uuid.uuid4()), "Qty":int(caps_qty), "Item":"Safety Caps", "Unit":"EA",
                      "Price Each":float(caps_unit_cost), "Line Total":float(caps_unit_cost)*int(caps_qty)})
    if removal_selected and customer_qty>0:
        lines.append({"_id":str(uuid.uuid4()), "Qty":customer_qty, "Item":"Fence Removal", "Unit":"LF",
                      "Price Each":float(removal_unit_lf), "Line Total":float(removal_unit_lf)*customer_qty})

    trig = (callback_context.triggered[0]["prop_id"] if callback_context.triggered else "")
    trig_id = trig.split(".")[0] if trig else None
    table_lines = preview_data if (trig_id == "preview_table" and isinstance(preview_data, list)) else lines

    def _lt(row):
        try:
            return float(row.get("Line Total") or (float(row.get("Qty") or 0)*float(row.get("Price Each") or 0)))
        except Exception:
            return 0.0
    subtotal = sum(_lt(r) for r in table_lines)
    sales_tax = 0.0 if remove_tax_flag else subtotal*SALES_TAX
    grand_total = subtotal + sales_tax
    totals_html = html.Div([
        html.Div(f"Subtotal: ${subtotal:,.2f}"),
        html.Div(f"Sales Tax ({0 if remove_tax_flag else SALES_TAX*100:.2f}%){' (removed)' if remove_tax_flag else ''}: ${sales_tax:,.2f}"),
        html.Div(html.Strong(f"Grand Total: ${grand_total:,.2f}")),
    ])

    # ---- Material takeoff table (posts / rolls / caps) ----
    materials = []
    if customer_qty>0:
        materials.append({"Qty": int(rolls), "Item": "Fabric Roll (100 LF)", "Unit": "ROLL", "Notes": f"For ~{int(req_ft):,} LF incl. waste"})
    if posts>0:
        materials.append({"Qty": int(posts), "Item": "T-Post", "Unit": "EA", "Notes": f"Spacing {post_spacing} ft"})
    if caps_qty>0:
        materials.append({"Qty": int(caps_qty), "Item": "Safety Cap", "Unit": "EA", "Notes": ("OSHA" if cap_type=='OSHA' else "Plastic")})

    return cs, tc, mc, badge, fig, table_lines, totals_html, materials

# ---- CSV Downloads ----
@app.callback(
    Output("download_customer_csv","data"),
    Input("btn_download_customer","n_clicks"),
    State("preview_table","data"),
    prevent_initial_call=True
)
def download_customer(n, table_rows):
    df = pd.DataFrame(table_rows or [])
    return send_data_frame(df.to_csv, "customer_printout.csv", index=False)

@app.callback(
    Output("download_materials_csv","data"),
    Input("btn_download_materials","n_clicks"),
    State("materials_table","data"),
    prevent_initial_call=True
)
def download_materials(n, mats):
    df = pd.DataFrame(mats or [])
    return send_data_frame(df.to_csv, "materials_takeoff.csv", index=False)

# ---- PDF Download ----
def _build_quote_pdf(buf, proj, company, address, lines, mats, tax_rate):
    styles = getSampleStyleSheet()
    h1 = styles['Heading1']; h1.fontSize = 16
    h2 = styles['Heading2']; h2.fontSize = 12
    normal = styles['BodyText']
    subtle = ParagraphStyle('subtle', parent=normal, textColor=colors.grey, fontSize=9)

    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=36)
    story = []

    today = datetime.now().strftime("%b %d, %Y")
    story.append(Paragraph("Double Oak Fencing — Quote", h1))
    story.append(Paragraph(f"Date: {today}", subtle))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Project:</b> {proj or 'Fencing'}<br/><b>Customer:</b> {company or '-'}<br/><b>Address:</b> {address or '-'}", normal))
    story.append(Spacer(1, 12))

    # Customer Printout table
    story.append(Paragraph("Customer Printout", h2))
    df = pd.DataFrame(lines or [])
    if not df.empty:
        df_disp = df[['Qty','Item','Unit','Price Each','Line Total']].copy()
        tbl = Table([list(df_disp.columns)] + df_disp.values.tolist(), hAlign='LEFT', colWidths=[50, 220, 50, 80, 80])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.Color(0.18,0.43,0.20)),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('ALIGN', (0,0), (-1,0), 'LEFT'),
            ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
            ('ALIGN', (-2,1), (-1,-1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.Color(0.96,0.99,0.96)])
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph("No line items.", subtle))
    story.append(Spacer(1, 10))

    # Totals
    def _lt(row):
        try:
            return float(row.get("Line Total") or (float(row.get("Qty") or 0)*float(row.get("Price Each") or 0)))
        except Exception:
            return 0.0
    subtotal = sum(_lt(r) for r in (lines or []))
    sales_tax = subtotal*tax_rate
    grand_total = subtotal + sales_tax
    story.append(Paragraph(f"<b>Subtotal:</b> ${subtotal:,.2f}", normal))
    story.append(Paragraph(f"<b>Sales Tax ({tax_rate*100:.2f}%):</b> ${sales_tax:,.2f}", normal))
    story.append(Paragraph(f"<b>Grand Total:</b> ${grand_total:,.2f}", normal))
    story.append(Spacer(1, 14))

    # Materials
    story.append(Paragraph("Material Takeoff", h2))
    md = pd.DataFrame(mats or [])
    if not md.empty:
        md_disp = md[['Qty','Item','Unit','Notes']].copy()
        mt = Table([list(md_disp.columns)] + md_disp.values.tolist(), hAlign='LEFT', colWidths=[50, 220, 50, 160])
        mt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.Color(0.18,0.43,0.20)),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('ALIGN', (0,0), (-1,0), 'LEFT'),
            ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.Color(0.96,0.99,0.96)])
        ]))
        story.append(mt)
    else:
        story.append(Paragraph("No materials.", subtle))

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"Generated with Double Oak Fencing Estimator v{_read_version_fallback()}",
        ParagraphStyle('footer', fontSize=8, textColor=colors.grey, alignment=2)  # right aligned
    ))

    doc.build(story)

@app.callback(
    Output("download_pdf","data"),
    Input("btn_download_pdf","n_clicks"),
    State("preview_table","data"),
    State("materials_table","data"),
    State("project_name","value"),
    State("company_name","value"),
    State("project_address","value"),
    prevent_initial_call=True
)
def download_pdf(n, lines, mats, proj, company, address):
    def _writer(f):
        _build_quote_pdf(f, proj, company, address, lines or [], mats or [], SALES_TAX)
    return send_bytes(_writer, f"quote_{(proj or 'fencing').replace(' ','_').lower()}.pdf")

# ---- Mailto link (compose email with totals) ----
@app.callback(
    Output("mailto_link","href"),
    Input("preview_table","data"),
    Input("materials_table","data"),
    Input("project_name","value"),
    Input("company_name","value"),
    Input("project_address","value"),
)
def mailto_href(lines, mats, project_name, company_name, project_address):
    lines = lines or []; mats = mats or []
    sub = f"Quote - {project_name or 'Fencing'}"
    body = "Hello,%0D%0A%0D%0A"
    body += f"Please find the quote below for {company_name or 'your project'} at {project_address or 'the jobsite'}.%0D%0A%0D%0A"
    def row(r): return f"{r.get('Qty','')} {r.get('Unit','')} – {r.get('Item','')} @ ${r.get('Price Each','')}: ${r.get('Line Total','')}"
    body += "Customer Printout:%0D%0A" + "%0D%0A".join(row(r) for r in lines) + "%0D%0A%0D%0A"
    def mrow(r): return f"{r.get('Qty','')} {r.get('Unit','')} – {r.get('Item','')} ({r.get('Notes','')})"
    body += "Materials Takeoff:%0D%0A" + "%0D%0A".join(mrow(r) for r in mats)
    return f"mailto:?subject={_url.quote(sub)}&body={body}"

server = app.server  # expose Flask server for gunicorn/Render

if __name__ == "__main__":
    app.run_server(host="0.0.0.0", port=8050, debug=True)
