import os
import secrets
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
import yfinance as yf

_security = HTTPBasic()
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "1234")


def verify_password(credentials: HTTPBasicCredentials = Depends(_security)):
    # secrets.compare_digest prevents timing attacks
    password_ok = secrets.compare_digest(
        credentials.password.encode(), _APP_PASSWORD.encode()
    )
    if not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="パスワードが正しくありません",
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(dependencies=[Depends(verify_password)])

# Absolute path so Vercel's Lambda can locate templates regardless of cwd
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))

BANK_RATE = 1.5  # % annual fixed deposit rate for comparison


def normalize_symbol(code: str) -> str:
    code = code.strip().upper()
    if not code.endswith(".TW") and not code.endswith(".TWO"):
        code = code + ".TW"
    return code


def fetch_info(symbol: str) -> dict:
    ticker = yf.Ticker(symbol)
    info = ticker.info
    if not info.get("regularMarketPrice") and not info.get("currentPrice"):
        raise ValueError(f"找不到股票代號 {symbol} 的資料")
    return info


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/stock-check", response_class=HTMLResponse)
async def stock_check(request: Request, stock_code: str = Form(...)):
    symbol = normalize_symbol(stock_code)
    try:
        info = fetch_info(symbol)

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        pe = info.get("trailingPE")
        eps = info.get("trailingEps")
        # trailingAnnualDividendYield is in decimal form (0.0101 = 1.01%)
        # dividendYield from yfinance is inconsistently in percent form for TW stocks
        dy = info.get("trailingAnnualDividendYield") or info.get("dividendYield")
        name = info.get("longName") or info.get("shortName") or symbol

        # If value is > 1 it's already a percentage (e.g. 1.01), otherwise multiply
        if dy and dy > 1:
            dy_pct = round(dy, 2)
        else:
            dy_pct = round(dy * 100, 2) if dy else None

        if pe is None:
            badge_label = "無本益比資料"
            badge_class = "bg-gray-100 text-gray-700"
        elif pe < 15:
            badge_label = "便宜價 🟢"
            badge_class = "bg-green-100 text-green-800"
        elif pe <= 25:
            badge_label = "合理價 🟡"
            badge_class = "bg-yellow-100 text-yellow-800"
        else:
            badge_label = "昂貴價 🔴"
            badge_class = "bg-red-100 text-red-800"

        return templates.TemplateResponse(
            request,
            "partials/stock_result.html",
            {
                "symbol": symbol,
                "name": name,
                "price": f"{price:,.2f}" if price else "N/A",
                "pe": f"{pe:.1f}" if pe else "N/A",
                "eps": f"{eps:.2f}" if eps else None,
                "dy_pct": f"{dy_pct:.2f}" if dy_pct else "N/A",
                "badge_label": badge_label,
                "badge_class": badge_class,
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            request,
            "partials/error.html",
            {"message": f"無法取得 {symbol} 的資料，請確認股票代號是否正確。({e})"},
        )


@app.post("/dividend-calc", response_class=HTMLResponse)
async def dividend_calc(
    request: Request,
    stock_code: str = Form(...),
    shares_zhang: float = Form(...),
    cost_per_share: float = Form(...),
):
    symbol = normalize_symbol(stock_code)
    try:
        info = fetch_info(symbol)

        name = info.get("longName") or info.get("shortName") or symbol
        dividend_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")

        if not dividend_rate:
            raise ValueError("該股票目前無配息資料")

        total_shares = int(shares_zhang * 1000)
        total_investment = total_shares * cost_per_share
        annual_dividend = total_shares * dividend_rate
        actual_yield = (dividend_rate / cost_per_share) * 100
        monthly_equiv = annual_dividend / 12
        multiplier = actual_yield / BANK_RATE

        return templates.TemplateResponse(
            request,
            "partials/dividend_result.html",
            {
                "symbol": symbol,
                "name": name,
                "shares_zhang": int(shares_zhang),
                "total_shares": f"{total_shares:,}",
                "total_investment": f"{total_investment:,.0f}",
                "actual_yield": f"{actual_yield:.2f}",
                "annual_dividend": f"{annual_dividend:,.0f}",
                "monthly_equiv": f"{monthly_equiv:,.0f}",
                "multiplier": f"{multiplier:.1f}",
                "bank_rate": BANK_RATE,
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            request,
            "partials/error.html",
            {"message": f"無法計算 {symbol} 的配息，請確認股票代號是否正確。({e})"},
        )


@app.post("/profit-calc", response_class=HTMLResponse)
async def profit_calc(
    request: Request,
    stock_code: str = Form(...),
    buy_price: float = Form(...),
    target_pct: float = Form(...),
):
    symbol = normalize_symbol(stock_code)
    target_price = buy_price * (1 + target_pct / 100)
    profit_per_share = target_price - buy_price

    # Try to fetch current price; non-fatal if unavailable
    current_price = None
    name = symbol
    gap_pct = None
    already_reached = False
    try:
        info = fetch_info(symbol)
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        name = info.get("longName") or info.get("shortName") or symbol
        if current_price:
            gap_pct = (target_price - current_price) / current_price * 100
            already_reached = current_price >= target_price
    except Exception:
        pass

    return templates.TemplateResponse(
        request,
        "partials/profit_result.html",
        {
            "symbol": symbol,
            "name": name,
            "buy_price": f"{buy_price:,.2f}",
            "target_pct": target_pct,
            "target_price": f"{target_price:,.2f}",
            "profit_per_share": f"{profit_per_share:,.2f}",
            "current_price": f"{current_price:,.2f}" if current_price else None,
            "gap_pct": f"{gap_pct:.1f}" if gap_pct is not None else None,
            "already_reached": already_reached,
        },
    )
