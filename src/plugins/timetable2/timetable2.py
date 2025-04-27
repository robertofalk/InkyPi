import logging
import datetime
import requests

from types import SimpleNamespace

from deutsche_bahn_api.api_authentication import ApiAuthentication
from deutsche_bahn_api.station_helper import StationHelper
from deutsche_bahn_api.timetable_helper import TimetableHelper

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class Timetable2(BasePlugin):
    def __init__(self, config, **dependencies) -> None:
        super().__init__(config, **dependencies)

        self.station = "Heidelberg-Kirchheim/Rohrbach"
        api = ApiAuthentication("d94981285852ebe2171442cb28e0d49c", "67dedde8868e4add4535f0cdd193b2d0")
        station_helper = StationHelper()
        found_stations_by_name = station_helper.find_stations_by_name("Kronau")

        self.timetable_helper = TimetableHelper(found_stations_by_name[0], api)
        self.trains_cache = []
        self.last_fetch_timestamp = datetime.datetime.now() - datetime.timedelta(hours=1)
        self._update_trains()


    def _update_trains(self):
        self.last_fetch_timestamp = self.last_fetch_timestamp if self.last_fetch_timestamp else datetime.datetime.now() - datetime.timedelta(hours=1)
        trains = []
        now = datetime.datetime.now()
        difference = now - self.last_fetch_timestamp
        hour = self.last_fetch_timestamp.hour

        if difference.total_seconds() > 3600: # 1 hour
            logger.info(f"Loading trains for {now}")
            self.last_fetch_timestamp = now
            self.trains_cache.clear()

            logger.info(f"Fetching timetable for hour: {hour}")
            trains = self.timetable_helper.get_timetable(hour=hour)

        if self.last_fetch_timestamp.minute >= 30:

            if hour == 23:
                hour = 0
                next_day = now + datetime.timedelta(days=1)
            else:
                hour += 1
                next_day = now

            logger.info(f"Fetching timetable for hour: {hour}, day: {next_day}")
            trains.extend(self.timetable_helper.get_timetable(hour=hour, date=next_day))

        if trains:
            for train in reversed(trains):
                if self.station in train.passed_stations or self.station not in train.stations:
                    trains.remove(train)
                    continue

                train.arrival_dt = datetime.datetime.strptime(train.arrival, '%y%m%d%H%M')

            trains.sort(key=lambda t: t.arrival_dt)
            self.trains_cache.extend(trains)
            logger.info(f"Updated train cache: {len(self.trains_cache)} trains")

        outdated = False
        for train in reversed(self.trains_cache):

            if outdated:
                logger.info(f"Train from {train.arrival_dt} removed")
                self.trains_cache.remove(train)
            else:
                time_diff = now - train.arrival_dt
                if time_diff.total_seconds() > 0:
                    outdated = True
    
    def _check_for_train_changes(self):
        logger.info("Loading train changes")
        trains_with_changes = self.timetable_helper.get_timetable_changes(self.trains_cache)

        trains = []
        for train in self.trains_cache:
            original_arrival = train.arrival_dt

            train_data = SimpleNamespace()

            train_data.line = f"{train.train_type}{train.train_line}"
            train_data.platform = train.platform
            train_data.time = original_arrival.strftime('%H:%M')
            train_data.delay = 0

            changes = next((change for change in trains_with_changes if change.train_number == train.train_number), None)
            if changes:
                if hasattr(changes, 'train_changes'):
                    change_arrival = datetime.datetime.strptime(changes.train_changes.arrival, '%y%m%d%H%M')
                    train_data.delay = (change_arrival - original_arrival).total_seconds() / 60

            trains.append(train_data)

            if len(trains) > 3:
                break
        return trains
    
    def _get_weather_icon(self, weather_code):
        icon = None
        
        if weather_code == 0:
            icon = "clear.png"
        elif weather_code in [1, 2]:
            icon = "partly.png"
        elif weather_code == 3:
            icon = "cloudy.png"
        elif weather_code in [45, 48]:
            icon = "fog.png"
        elif weather_code in [51, 53, 55, 56, 57]:
            icon = "drizzle.png"
        elif weather_code in [61, 63, 65, 66, 67]:
            icon = "rain.png"
        elif weather_code in [71, 73, 75, 77]:
            icon = "snow.png"
        elif weather_code in [80, 81, 82]:
            icon = "rain.png"
        elif weather_code in [95, 96, 99]:
            icon = "thunderstorm.png"
        
        return self.get_plugin_dir(f'icons/{icon}')

    def _get_weather_data(self):
        url = "https://api.open-meteo.com/v1/forecast?latitude=49.2012&longitude=8.6418&daily=temperature_2m_max,temperature_2m_min,weather_code,sunset,sunrise&hourly=temperature_2m,precipitation&current=temperature_2m,apparent_temperature,is_day,weather_code&timezone=Europe%2FBerlin&forecast_days=4"
        response = requests.get(url)
        if response.status_code != 200:
            logger.error(f"Failed to fetch weather data: {response.status_code}")
            RuntimeError(f"Failed to fetch weather data: {response.status_code}")

        weather_data = response.json()
        logger.info(f"Weather data: {weather_data}")            
        
        weather_code = weather_data.get("current").get("weather_code")

        display_data = SimpleNamespace()
        display_data.current_icon = self._get_weather_icon(weather_code)
        
        display_data.feels_like = str(round(weather_data.get("current").get("apparent_temperature")))
        display_data.current_temperature = str(round(weather_data.get("current").get("temperature_2m")))

        display_data.data_points = []

        sunrise_dt = datetime.datetime.strptime(weather_data.get("daily").get("sunrise")[0], '%Y-%m-%dT%H:%M')
        display_data.data_points.append({
            "label": "Sunrise",
            "measurement": sunrise_dt.strftime('%H:%M').lstrip("0"),
            "icon": self.get_plugin_dir('icons/sunrise.png')
        })

        sunset_dt = datetime.datetime.strptime(weather_data.get("daily").get("sunset")[0], '%Y-%m-%dT%H:%M')
        display_data.data_points.append({
            "label": "Sunset",
            "measurement": sunset_dt.strftime('%H:%M').lstrip("0"),
            "icon": self.get_plugin_dir('icons/sunset.png')
        })

        forecast = []
        for day in range(1, 4):
            next_day = datetime.datetime.strptime(weather_data.get("daily").get("time")[day], '%Y-%m-%d')
            day_forecast = {
                "day": next_day.strftime("%A"),
                "high": int(weather_data.get("daily").get("temperature_2m_max")[day]),
                "low": int(weather_data.get("daily").get("temperature_2m_min")[day]),
                "icon": self._get_weather_icon(weather_data.get("daily").get("weather_code")[day])
            }
            forecast.append(day_forecast)
        display_data.forecast = forecast

        hourly = []
        for hour in range(24):
            dt = datetime.datetime.strptime(weather_data.get("hourly").get("time")[hour], '%Y-%m-%dT%H:%M')
            hour_forecast = {
                "time": dt.strftime("%-I %p"),
                "temperature": int(weather_data.get("hourly").get("temperature_2m")[hour]),
                "precipitiation": weather_data.get("hourly").get("precipitation")[hour]
            }
            hourly.append(hour_forecast)
        display_data.hourly_forecast = hourly
        return display_data

    def generate_image(self, settings, device_config):
        
        display_data = self._get_weather_data()
        trains = self._check_for_train_changes()

        template_params = {
            "location": "Bad Schönborn",
            "current_date": datetime.datetime.now().strftime("%A, %B %d"),
            "trains": trains,
            "plugin_settings": settings,
            "current_temperature": display_data.current_temperature,
            "feels_like": display_data.feels_like,
            "current_day_icon": display_data.current_icon,
            "temperature_unit": "°C",
            "data_points": display_data.data_points,
            "hourly_forecast": display_data.hourly_forecast,
            "forecast": display_data.forecast
        }

        dimensions = device_config.get_resolution()
        image = self.render_image(
            dimensions=dimensions,
            html_file="timetable2.html",
            css_file="timetable2.css",
            template_params=template_params
        )
        return image