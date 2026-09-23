from app.config import get_settings
from app.providers.base import RoutingProvider, WeatherProvider
from app.providers.routing import MockRoutingProvider, OpenRouteServiceProvider
from app.providers.weather import MockWeatherProvider, OpenWeatherMapProvider


def get_routing_provider() -> RoutingProvider:
    settings = get_settings()
    if settings.ors_api_key:
        return OpenRouteServiceProvider(settings.ors_api_key)
    return MockRoutingProvider()


def get_weather_provider() -> WeatherProvider:
    settings = get_settings()
    if settings.owm_api_key:
        return OpenWeatherMapProvider(settings.owm_api_key)
    return MockWeatherProvider()
