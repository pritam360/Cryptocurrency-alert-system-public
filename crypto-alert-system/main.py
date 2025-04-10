from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Dict
import json
from requests import Session
from requests.exceptions import ConnectionError, Timeout, TooManyRedirects
from google.cloud import pubsub_v1
import uuid
import logging
import aiohttp
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Custom JSON encoder for datetime
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# Initialize FastAPI with metadata
app = FastAPI(
    title="Crypto Alert System",
    description="API for cryptocurrency price alerts",
    version="1.0.0"
)

# Configure templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration
CMC_API_KEY = '0678708a-7b8c-42c7-873b-432cc2360d03'
CMC_BASE_URL = 'https://pro-api.coinmarketcap.com'
PUBSUB_TOPIC = 'crypto-price-updates'
PROJECT_ID = 'crypto-alert-444022'

# Pydantic models
class AlertRequest(BaseModel):
    user_id: str = Field(..., description="Unique identifier for the user")
    cryptocurrency_id: str = Field(..., description="CoinMarketCap ID of the cryptocurrency")
    target_price: float = Field(..., gt=0, description="Target price for the alert")
    condition_type: str = Field(..., description="Alert condition: 'above' or 'below'")
    notification_type: str = Field(..., description="Notification method: 'email' or 'sms'")
    email: str = Field(..., description="Email address for notifications")

    class Config:
        schema_extra = {
            "example": {
                "user_id": "user123",
                "cryptocurrency_id": "1",
                "target_price": 50000.00,
                "condition_type": "above",
                "notification_type": "email",
                "email": "user@example.com"
            }
        }

class Alert(AlertRequest):
    alert_id: str
    created_at: datetime

class CryptoPrice(BaseModel):
    id: int
    name: str
    symbol: str
    price: float
    last_updated: datetime

class CryptoInfo(BaseModel):
    id: int
    name: str
    symbol: str
    slug: str
    is_active: int

# CoinMarketCap API Client
class CMCClient:
    def __init__(self):
        self.session = Session()
        self.session.headers.update({
            'Accepts': 'application/json',
            'X-CMC_PRO_API_KEY': CMC_API_KEY
        })

    async def get_crypto_info(self, crypto_id: str) -> Dict:
        """Get cryptocurrency metadata using v2 endpoint"""
        url = f"{CMC_BASE_URL}/v2/cryptocurrency/info"
        try:
            response = self.session.get(url, params={'id': crypto_id})
            response.raise_for_status()
            return response.json()['data'][crypto_id]
        except (ConnectionError, Timeout, TooManyRedirects) as e:
            logger.error(f"CMC API Connection Error: {str(e)}")
            raise HTTPException(status_code=503, detail="CoinMarketCap API is currently unavailable")
        except KeyError as e:
            logger.error(f"Invalid cryptocurrency ID: {crypto_id}")
            raise HTTPException(status_code=404, detail=f"Cryptocurrency with ID {crypto_id} not found")
        except Exception as e:
            logger.error(f"Unexpected error in get_crypto_info: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    async def get_latest_price(self, crypto_id: str) -> Dict:
        """Get latest cryptocurrency price using v2 endpoint"""
        url = f"{CMC_BASE_URL}/v2/cryptocurrency/quotes/latest"
        try:
            response = self.session.get(url, params={
                'id': crypto_id,
                'convert': 'USD'
            })
            response.raise_for_status()
            data = response.json()['data'][crypto_id]
            return {
                'id': data['id'],
                'name': data['name'],
                'symbol': data['symbol'],
                'price': data['quote']['USD']['price'],
                'last_updated': data['last_updated']
            }
        except (ConnectionError, Timeout, TooManyRedirects) as e:
            logger.error(f"CMC API Connection Error: {str(e)}")
            raise HTTPException(status_code=503, detail="CoinMarketCap API is currently unavailable")
        except KeyError as e:
            logger.error(f"Invalid cryptocurrency ID: {crypto_id}")
            raise HTTPException(status_code=404, detail=f"Cryptocurrency with ID {crypto_id} not found")
        except Exception as e:
            logger.error(f"Unexpected error in get_latest_price: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    async def get_crypto_map(self) -> List[Dict]:
        """Get cryptocurrency ID mappings using v1 endpoint"""
        url = f"{CMC_BASE_URL}/v1/cryptocurrency/map"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()['data']
        except (ConnectionError, Timeout, TooManyRedirects) as e:
            logger.error(f"CMC API Connection Error: {str(e)}")
            raise HTTPException(status_code=503, detail="CoinMarketCap API is currently unavailable")
        except Exception as e:
            logger.error(f"Unexpected error in get_crypto_map: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

# Initialize clients
cmc_client = CMCClient()
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)

# Web interface routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("create_alert.html", {"request": request})

# API endpoints
# @app.get("/cryptocurrencies")
# async def get_cryptocurrencies():
#     """Get list of available cryptocurrencies"""
#     try:
#         crypto_list = await cmc_client.get_crypto_map()
#         return sorted(crypto_list, key=lambda x: x['name'])
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

@app.get("/cryptocurrencies")
async def get_cryptocurrencies():
    """Get list of available cryptocurrencies with major coins first"""
    try:
        crypto_list = await cmc_client.get_crypto_map()
        
        # IDs for Bitcoin (1), Ethereum (1027), and Tether (825)
        priority_coins = {'1', '1027', '825'}
        
        # Split the list into priority and other coins
        priority_list = []
        other_list = []
        
        for crypto in crypto_list:
            if str(crypto['id']) in priority_coins:
                priority_list.append(crypto)
            else:
                other_list.append(crypto)
        
        # Sort each list by name
        priority_list = sorted(priority_list, key=lambda x: x['name'])
        other_list = sorted(other_list, key=lambda x: x['name'])
        
        # Combine the lists with priority coins first
        return priority_list + other_list
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/prices/{crypto_id}", response_model=CryptoPrice)
async def get_current_price(crypto_id: str):
    """Get current price for a cryptocurrency"""
    try:
        price_data = await cmc_client.get_latest_price(crypto_id)
        return CryptoPrice(**price_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_current_price: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/alerts", response_model=Alert, status_code=201)
async def create_alert(alert_request: AlertRequest):
    """Create a new price alert"""
    try:
        # Validate cryptocurrency exists
        await cmc_client.get_crypto_info(alert_request.cryptocurrency_id)
        
        # Get current price
        price_data = await cmc_client.get_latest_price(alert_request.cryptocurrency_id)
        
        # Create alert
        alert = Alert(
            **alert_request.dict(),
            alert_id=str(uuid.uuid4()),
            created_at=datetime.utcnow()
        )
        
        # Publish to Pub/Sub
        message_data = {
            **alert.dict(),
            'current_price': price_data['price']
        }
        try:
            publisher.publish(
                topic_path, 
                json.dumps(message_data, cls=DateTimeEncoder).encode('utf-8')
            )
            logger.info(f"Alert published to Pub/Sub: {alert.alert_id}")
        except Exception as e:
            logger.error(f"Failed to publish to Pub/Sub: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to process alert")
        
        return alert
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in create_alert: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)