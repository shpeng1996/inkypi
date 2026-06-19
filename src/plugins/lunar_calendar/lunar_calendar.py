import calendar
import logging
import os
from datetime import date, datetime, timedelta

import icalendar
import pytz
import requests

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.lunar_calendar.lunardate import LunarDate

logger = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={long}"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    "&timezone=auto&forecast_days=16{unit_params}"
)
IP_GEOLOCATION_URL = "https://ipapi.co/json/"
TAIWAN_HOLIDAY_ICS_URL = "https://www.opendata.vip/tool/holidayICS/TW/{year}"

UNIT_LABELS = {
    "metric": "°C",
    "imperial": "°F",
}

OPEN_METEO_UNIT_PARAMS = {
    "metric": "&temperature_unit=celsius",
    "imperial": "&temperature_unit=fahrenheit",
}

WEEKDAYS_EN = [
    {"label": "Sun", "key": "sun"},
    {"label": "Mon", "key": "mon"},
    {"label": "Tue", "key": "tue"},
    {"label": "Wed", "key": "wed"},
    {"label": "Thu", "key": "thu"},
    {"label": "Fri", "key": "fri"},
    {"label": "Sat", "key": "sat"},
]

LUNAR_MONTHS = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "臘"]
LUNAR_DAY_PREFIXES = ["初", "十", "廿", "卅"]
LUNAR_DAY_DIGITS = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

_WEATHER_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "weather", "icons")

WEATHER_TYPE_TO_ICON = {
    "sunny": "01d",
    "partly-cloudy": "02d",
    "cloudy": "04d",
    "rain-light": "51d",
    "rain": "53d",
    "rain-heavy": "09d",
    "snow": "13d",
    "storm": "11d",
}

WEATHER_ICON_PATHS = {
    wtype: os.path.join(_WEATHER_ICONS_DIR, f"{icon}.png")
    for wtype, icon in WEATHER_TYPE_TO_ICON.items()
}


class LunarCalendar(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = True
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone = device_config.get_config("timezone", default="Asia/Taipei")
        tz = pytz.timezone(timezone)
        today = datetime.now(tz).date()
        month_offset = self.parse_month_offset(settings.get("monthOffset", 0))
        target_month = self.shift_month(today, month_offset)
        units = self.parse_units(settings.get("units", "metric"))
        week_start_day = self.parse_week_start_day(settings.get("weekStartDay", 0))

        weather_by_date = {}
        if settings.get("displayWeather", "true") == "true":
            try:
                lat, long = self.get_coordinates(settings)
                weather_by_date = self.fetch_open_meteo_forecast(lat, long, units)
            except RuntimeError:
                raise
            except Exception as e:
                logger.error(f"Open-Meteo request failed: {e}")
                raise RuntimeError("Open-Meteo request failure, please check logs.")

        holiday_dates = set()
        if settings.get("displayTaiwanHolidays", "true") == "true":
            try:
                holiday_dates = self.fetch_taiwan_holidays(target_month.year)
            except Exception as e:
                logger.warning(f"Failed to fetch Taiwan holidays: {e}")

        weeks = self.build_month_weeks(
            target_month.year, target_month.month, week_start_day, weather_by_date, holiday_dates
        )

        template_params = {
            "title_month": target_month.strftime("%B"),
            "title_year": target_month.strftime("%Y"),
            "today_iso": today.isoformat(),
            "weekdays": self.rotate_weekdays(week_start_day),
            "weeks": weeks,
            "display_weather": settings.get("displayWeather", "true") == "true",
            "temperature_unit": UNIT_LABELS[units],
            "today_weather": weather_by_date.get(today.isoformat()),
            "weather_icons": WEATHER_ICON_PATHS,
            "plugin_settings": settings,
        }

        image = self.render_image(dimensions, "lunar_calendar.html", "lunar_calendar.css", template_params)
        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image

    def fetch_taiwan_holidays(self, year):
        """Returns dict mapping date string (YYYY-MM-DD) → holiday name."""
        url = TAIWAN_HOLIDAY_ICS_URL.format(year=year)
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        cal = icalendar.Calendar.from_ical(response.content)
        holidays = {}
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            summary = str(component.get("SUMMARY", ""))
            if "補行上班" in summary:
                continue
            dtstart = component.get("DTSTART")
            if not dtstart:
                continue
            dt = dtstart.dt
            day = dt.date() if hasattr(dt, "date") else dt
            holidays[day.isoformat()] = summary
        return holidays

    def build_month_weeks(self, year, month, week_start_day=0, weather_by_date=None, holiday_dates=None):
        weather_by_date = weather_by_date or {}
        holiday_dates = holiday_dates or {}
        first_day = date(year, month, 1)
        _, days_in_month = calendar.monthrange(year, month)
        leading_blanks = (self.weekday_sunday_first(first_day) - week_start_day) % 7

        cells = []
        for i in range(leading_blanks, 0, -1):
            prev_date = first_day - timedelta(days=i)
            cell = self.build_day_cell(prev_date, weather_by_date.get(prev_date.isoformat()), holiday_dates)
            cell["in_month"] = False
            cells.append(cell)

        for day in range(1, days_in_month + 1):
            current_date = date(year, month, day)
            cells.append(self.build_day_cell(current_date, weather_by_date.get(current_date.isoformat()), holiday_dates))

        last_day = date(year, month, days_in_month)
        trailing = (7 - len(cells) % 7) % 7
        for i in range(1, trailing + 1):
            next_date = last_day + timedelta(days=i)
            cell = self.build_day_cell(next_date, weather_by_date.get(next_date.isoformat()), holiday_dates)
            cell["in_month"] = False
            cells.append(cell)

        return [cells[i:i + 7] for i in range(0, len(cells), 7)]

    def build_day_cell(self, current_date, weather, holiday_dates=None):
        lunar_date = LunarDate.fromSolarDate(current_date.year, current_date.month, current_date.day)
        weekday = self.weekday_sunday_first(current_date)  # 0=Sun, 6=Sat
        return {
            "in_month": True,
            "date": current_date.isoformat(),
            "solar_day": current_date.day,
            "lunar": self.format_lunar_date(lunar_date),
            "weather": weather,
            "is_sunday": weekday == 0,
            "is_saturday": weekday == 6,
            "is_holiday": current_date.isoformat() in (holiday_dates or {}),
            "holiday_name": (holiday_dates or {}).get(current_date.isoformat(), ""),
        }

    def fetch_open_meteo_forecast(self, lat, long, units):
        url = OPEN_METEO_FORECAST_URL.format(
            lat=lat,
            long=long,
            unit_params=OPEN_METEO_UNIT_PARAMS[units],
        )
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        daily = response.json().get("daily", {})
        times = daily.get("time", [])
        weather_codes = daily.get("weather_code", [])
        high_temps = daily.get("temperature_2m_max", [])
        low_temps = daily.get("temperature_2m_min", [])

        forecast = {}
        for i, day in enumerate(times):
            code = weather_codes[i] if i < len(weather_codes) else 0
            high = high_temps[i] if i < len(high_temps) else None
            low = low_temps[i] if i < len(low_temps) else None
            forecast[day] = {
                "type": self.map_weather_code_to_type(code),
                "label": self.map_weather_code_to_label(code),
                "high": round(high) if high is not None else None,
                "low": round(low) if low is not None else None,
            }
        return forecast

    def get_coordinates(self, settings):
        latitude = settings.get("latitude")
        longitude = settings.get("longitude")

        if self.is_empty_coordinate(latitude) and self.is_empty_coordinate(longitude):
            return self.get_ip_location()

        if self.is_empty_coordinate(latitude) or self.is_empty_coordinate(longitude):
            raise RuntimeError("Latitude and Longitude are required.")

        try:
            return float(latitude), float(longitude)
        except (TypeError, ValueError):
            raise RuntimeError("Latitude and Longitude must be valid numbers.")

    def get_ip_location(self):
        try:
            response = requests.get(IP_GEOLOCATION_URL, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data["latitude"]), float(data["longitude"])
        except Exception as e:
            raise RuntimeError(f"Failed to determine location: {e}")

    def map_weather_code_to_type(self, weather_code):
        if weather_code in [0]:
            return "sunny"
        if weather_code in [1, 2]:
            return "partly-cloudy"
        if weather_code in [3, 45, 48]:
            return "cloudy"
        if weather_code in [51, 61, 80]:
            return "rain-light"
        if weather_code in [53, 63, 81]:
            return "rain"
        if weather_code in [55, 65, 82]:
            return "rain-heavy"
        if weather_code in [56, 57, 66, 67, 71, 73, 75, 77, 85, 86]:
            return "snow"
        if weather_code in [95, 96, 99]:
            return "storm"
        return "sunny"

    def map_weather_code_to_label(self, weather_code):
        weather_type = self.map_weather_code_to_type(weather_code)
        labels = {
            "sunny": "Sunny",
            "partly-cloudy": "Partly Cloudy",
            "cloudy": "Cloudy",
            "rain-light": "Light Rain",
            "rain": "Rain",
            "rain-heavy": "Heavy Rain",
            "snow": "Snow",
            "storm": "Thunderstorm",
        }
        return labels[weather_type]

    def format_lunar_date(self, lunar_date):
        month = LUNAR_MONTHS[lunar_date.month - 1]
        if lunar_date.day == 1:
            prefix = "閏" if lunar_date.isLeapMonth else ""
            return f"{prefix}{month}月"
        return self.format_lunar_day(lunar_date.day)

    def format_lunar_day(self, day):
        if day == 10:
            return "初十"
        if day == 20:
            return "二十"
        if day == 30:
            return "三十"
        prefix = LUNAR_DAY_PREFIXES[(day - 1) // 10]
        digit = LUNAR_DAY_DIGITS[day % 10]
        return f"{prefix}{digit}"

    def rotate_weekdays(self, week_start_day):
        return WEEKDAYS_EN[week_start_day:] + WEEKDAYS_EN[:week_start_day]

    def weekday_sunday_first(self, target_date):
        return (target_date.weekday() + 1) % 7

    def parse_units(self, units):
        if units not in UNIT_LABELS:
            raise RuntimeError("Units are required.")
        return units

    def parse_week_start_day(self, week_start_day):
        try:
            value = int(week_start_day)
        except (TypeError, ValueError):
            raise RuntimeError("Week start day must be Sunday or Monday.")
        if value not in [0, 1]:
            raise RuntimeError("Week start day must be Sunday or Monday.")
        return value

    def parse_month_offset(self, month_offset):
        try:
            value = int(month_offset or 0)
        except (TypeError, ValueError):
            raise RuntimeError("Month offset must be a number.")
        if value < -12 or value > 12:
            raise RuntimeError("Month offset must be between -12 and 12.")
        return value

    def shift_month(self, start_date, offset):
        month_index = start_date.year * 12 + start_date.month - 1 + offset
        year = month_index // 12
        month = month_index % 12 + 1
        if year < 1900 or year > 2099:
            raise RuntimeError("Lunar calendar supports years from 1900 to 2099.")
        return date(year, month, 1)

    def is_empty_coordinate(self, coordinate):
        return coordinate is None or str(coordinate).strip() == ""
