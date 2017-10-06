from django.utils import timezone
import requests

from dsmr_pvoutput.models.settings import PVOutputAddStatusSettings, PVOutputAPISettings
import dsmr_consumption.services
import dsmr_backend.services


def should_export():
    """ Checks whether we should export data yet, for Add Status calls. """
    api_settings = PVOutputAPISettings.get_solo()
    status_settings = PVOutputAddStatusSettings.get_solo()

    # Only when enabled and credentials set.
    if not status_settings.export or not api_settings.auth_token or not api_settings.system_identifier:
        return False

    # Nonsense when having no data.
    capabilities = dsmr_backend.services.get_capabilities()

    if not capabilities['electricity_returned']:
        print(' - [!] PVOutput | No electricity return recorded by application!')
        return False

    return dsmr_backend.services.is_timestamp_passed(timestamp=status_settings.next_export)


def schedule_next_export():
    """ Schedules the next export, according to user preference. """
    status_settings = PVOutputAddStatusSettings.get_solo()

    next_export = timezone.now() + timezone.timedelta(minutes=status_settings.upload_interval)
    print(' - PVOutput | Delaying the next export until: {}'.format(next_export))

    status_settings.next_export = next_export
    status_settings.save()


def export():
    """ Exports data to PVOutput, calling Add Status. """
    if not should_export():
        return

    api_settings = PVOutputAPISettings.get_solo()
    status_settings = PVOutputAddStatusSettings.get_solo()

    # Find the latest consumption within the interval
    local_now = timezone.localtime(timezone.now())

    try:
        day_consumption = dsmr_consumption.services.day_consumption(day=local_now)
    except LookupError:
        print(' [!] PVOutput: No data found for {}'.format(local_now))
        return schedule_next_export()

    latest_consumption = day_consumption['latest_consumption']
    consumption_timestamp = timezone.localtime(latest_consumption.read_at)

    data = {
        'd': consumption_timestamp.date().strftime('%Y%m%d'),
        't': consumption_timestamp.time().strftime('%H:%M'),
        'v3': int(day_consumption['electricity_merged'] * 1000),  # Energy Consumption (Wh)
        'v4': int(latest_consumption.currently_delivered * 1000),  # Power Consumption (W)
        'n': 1,  # Net Flag, always enabled for smart meters
    }

    # Optional, paid PVOutput feature.
    if status_settings.delay:
        data.update({'delay': status_settings.delay})

    print(' - PVOutput | Uploading data @ {}'.format(data['t']))
    response = requests.post(
        PVOutputAddStatusSettings.API_URL,
        headers={
            'X-Pvoutput-Apikey': api_settings.auth_token,
            'X-Pvoutput-SystemId': api_settings.system_identifier,
        },
        data=data
    )

    if response.status_code != 200:
        print(' [!] PVOutput upload failed (HTTP {}): {}'.format(response.status_code, response.text))

    schedule_next_export()
