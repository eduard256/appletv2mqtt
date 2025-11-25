#!/usr/bin/env python3
"""
Apple TV to MQTT Bridge
Simple, stable, and reliable MQTT bridge for Apple TV control.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import traceback
from typing import Optional

from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import pyatv
from pyatv.const import Protocol
from pyatv.interface import AppleTV


# =============================================================================
# Configuration
# =============================================================================

class Config:
    """Configuration loaded from environment variables."""

    REQUIRED_VARS = [
        'MQTT_HOST', 'MQTT_PORT', 'MQTT_USER', 'MQTT_PASSWORD', 'MQTT_QOS', 'MQTT_BASE_TOPIC',
        'APPLETV_ID', 'APPLETV_CREDENTIALS', 'APPLETV_ADDRESS',
        'STATE_UPDATE_INTERVAL', 'APPS_UPDATE_INTERVAL',
        'MQTT_RECONNECT_DELAY', 'APPLETV_RECONNECT_DELAY',
        'LOG_LEVEL'
    ]

    def __init__(self):
        load_dotenv()
        self._validate()
        self._load()

    def _validate(self):
        """Check that all required environment variables are set."""
        missing = [var for var in self.REQUIRED_VARS if not os.getenv(var)]
        if missing:
            print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
            print("Please check your .env file. See .env.example for reference.")
            sys.exit(1)

    def _load(self):
        """Load configuration from environment variables."""
        # MQTT settings
        self.mqtt_host = os.getenv('MQTT_HOST')
        self.mqtt_port = int(os.getenv('MQTT_PORT'))
        self.mqtt_user = os.getenv('MQTT_USER')
        self.mqtt_password = os.getenv('MQTT_PASSWORD')
        self.mqtt_qos = int(os.getenv('MQTT_QOS'))
        self.mqtt_base_topic = os.getenv('MQTT_BASE_TOPIC')

        # Apple TV settings
        self.appletv_id = os.getenv('APPLETV_ID')
        self.appletv_credentials = os.getenv('APPLETV_CREDENTIALS')
        self.appletv_address = os.getenv('APPLETV_ADDRESS')

        # Intervals
        self.state_update_interval = int(os.getenv('STATE_UPDATE_INTERVAL'))
        self.apps_update_interval = int(os.getenv('APPS_UPDATE_INTERVAL'))

        # Reconnection delays
        self.mqtt_reconnect_delay = int(os.getenv('MQTT_RECONNECT_DELAY'))
        self.appletv_reconnect_delay = int(os.getenv('APPLETV_RECONNECT_DELAY'))

        # Logging
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

    @property
    def topic_availability(self) -> str:
        return f"{self.mqtt_base_topic}/availability"

    @property
    def topic_state(self) -> str:
        return f"{self.mqtt_base_topic}/state"

    @property
    def topic_apps(self) -> str:
        return f"{self.mqtt_base_topic}/apps"

    @property
    def topic_set(self) -> str:
        return f"{self.mqtt_base_topic}/set"

    @property
    def topic_get(self) -> str:
        return f"{self.mqtt_base_topic}/get"


# =============================================================================
# Global state
# =============================================================================

config: Optional[Config] = None
logger: Optional[logging.Logger] = None
mqtt_client: Optional[mqtt.Client] = None
atv: Optional[AppleTV] = None
shutdown_event: Optional[asyncio.Event] = None
command_queue: Optional[asyncio.Queue] = None
main_loop: Optional[asyncio.AbstractEventLoop] = None


# =============================================================================
# Logging setup
# =============================================================================

def setup_logging(level: str) -> logging.Logger:
    """Configure logging with the specified level."""
    log = logging.getLogger('appletv2mqtt')
    log.setLevel(getattr(logging, level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level, logging.INFO))

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    log.addHandler(handler)
    return log


# =============================================================================
# MQTT Functions
# =============================================================================

def on_mqtt_connect(client, userdata, flags, reason_code, properties):
    """Callback when connected to MQTT broker."""
    if reason_code == 0:
        logger.info(f"Connected to MQTT broker at {config.mqtt_host}:{config.mqtt_port}")
        # Subscribe to command topics
        client.subscribe(config.topic_set, qos=config.mqtt_qos)
        client.subscribe(config.topic_get, qos=config.mqtt_qos)
        logger.info(f"Subscribed to {config.topic_set} and {config.topic_get}")
        # Publish online status
        client.publish(config.topic_availability, "online", qos=config.mqtt_qos, retain=True)
    else:
        logger.error(f"Failed to connect to MQTT broker: {reason_code}")


def on_mqtt_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    """Callback when disconnected from MQTT broker."""
    logger.warning(f"Disconnected from MQTT broker: {reason_code}")


def on_mqtt_message(client, userdata, msg):
    """Callback when MQTT message received."""
    try:
        payload = msg.payload.decode('utf-8')
        logger.debug(f"MQTT message received on {msg.topic}: {payload}")

        if command_queue and main_loop:
            # Put command in queue for async processing
            asyncio.run_coroutine_threadsafe(
                command_queue.put({'topic': msg.topic, 'payload': payload}),
                main_loop
            )
    except Exception as e:
        logger.error(f"Error processing MQTT message: {e}")


def setup_mqtt() -> mqtt.Client:
    """Setup and connect MQTT client."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"appletv2mqtt_{config.appletv_id.replace(':', '')}"
    )

    # Set callbacks
    client.on_connect = on_mqtt_connect
    client.on_disconnect = on_mqtt_disconnect
    client.on_message = on_mqtt_message

    # Set authentication
    if config.mqtt_user and config.mqtt_password:
        client.username_pw_set(config.mqtt_user, config.mqtt_password)

    # Set Last Will Testament
    client.will_set(config.topic_availability, "offline", qos=config.mqtt_qos, retain=True)

    return client


def connect_mqtt(client: mqtt.Client) -> bool:
    """Connect to MQTT broker with retry."""
    while not shutdown_event.is_set():
        try:
            logger.info(f"Connecting to MQTT broker at {config.mqtt_host}:{config.mqtt_port}...")
            client.connect(config.mqtt_host, config.mqtt_port, keepalive=60)
            client.loop_start()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            logger.info(f"Retrying in {config.mqtt_reconnect_delay} seconds...")
            for _ in range(config.mqtt_reconnect_delay):
                if shutdown_event.is_set():
                    return False
                asyncio.get_event_loop().run_until_complete(asyncio.sleep(1))
    return False


def mqtt_publish(topic: str, payload: str, retain: bool = False):
    """Publish message to MQTT topic."""
    try:
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish(topic, payload, qos=config.mqtt_qos, retain=retain)
            logger.debug(f"Published to {topic}: {payload[:100]}...")
        else:
            logger.warning("MQTT client not connected, skipping publish")
    except Exception as e:
        logger.error(f"Error publishing to MQTT: {e}")


# =============================================================================
# Apple TV Functions
# =============================================================================

async def connect_appletv() -> Optional[AppleTV]:
    """Connect to Apple TV with retry."""
    global atv

    while not shutdown_event.is_set():
        try:
            logger.info(f"Connecting to Apple TV at {config.appletv_address}...")

            # Scan for device
            atvs = await pyatv.scan(
                asyncio.get_event_loop(),
                identifier=config.appletv_id,
                timeout=10
            )

            if not atvs:
                logger.warning(f"Apple TV with ID {config.appletv_id} not found")
                logger.info(f"Retrying in {config.appletv_reconnect_delay} seconds...")
                await asyncio.sleep(config.appletv_reconnect_delay)
                continue

            atv_config = atvs[0]

            # Set credentials
            protocol, credentials = config.appletv_credentials.split(':', 1)
            protocol_enum = Protocol[protocol.upper()] if hasattr(Protocol, protocol.upper()) else Protocol.Companion
            atv_config.set_credentials(protocol_enum, credentials)

            # Connect
            atv = await pyatv.connect(atv_config, asyncio.get_event_loop())

            logger.info(f"Connected to Apple TV: {atv.device_info.model}")
            logger.info(f"  OS: {atv.device_info.operating_system} {atv.device_info.version}")

            return atv

        except Exception as e:
            logger.error(f"Failed to connect to Apple TV: {e}")
            logger.debug(traceback.format_exc())
            logger.info(f"Retrying in {config.appletv_reconnect_delay} seconds...")
            await asyncio.sleep(config.appletv_reconnect_delay)

    return None


async def get_state() -> dict:
    """Get current playback state from Apple TV."""
    state = {
        'media_type': None,
        'device_state': None,
        'title': None,
        'artist': None,
        'album': None,
        'genre': None,
        'position': None,
        'total_time': None,
        'repeat': None,
        'shuffle': None,
        'app': None,
        'app_id': None,
        'power_state': None
    }

    try:
        if not atv:
            return state

        playing = await atv.metadata.playing()

        state['media_type'] = str(playing.media_type).split('.')[-1] if playing.media_type else None
        state['device_state'] = str(playing.device_state).split('.')[-1] if playing.device_state else None
        state['title'] = playing.title
        state['artist'] = playing.artist
        state['album'] = playing.album
        state['genre'] = playing.genre
        state['position'] = playing.position
        state['total_time'] = playing.total_time
        state['repeat'] = str(playing.repeat).split('.')[-1] if playing.repeat else None
        state['shuffle'] = str(playing.shuffle).split('.')[-1] if playing.shuffle else None

        # Get current app (separate API call, not part of Playing object)
        try:
            app = atv.metadata.app
            state['app'] = app.name if app else None
            state['app_id'] = app.identifier if app else None
        except Exception:
            state['app'] = None
            state['app_id'] = None

        # Get power state
        if hasattr(atv, 'power') and atv.power:
            state['power_state'] = str(atv.power.power_state).split('.')[-1]

        logger.debug(f"State: {state}")

    except Exception as e:
        logger.error(f"Error getting state: {e}")
        logger.debug(traceback.format_exc())

    return state


async def get_apps() -> list:
    """Get list of installed apps from Apple TV."""
    apps = []

    try:
        if not atv or not hasattr(atv, 'apps') or not atv.apps:
            return apps

        app_list = await atv.apps.app_list()
        apps = [{'name': app.name, 'id': app.identifier} for app in app_list]

        logger.debug(f"Apps: {len(apps)} found")

    except Exception as e:
        logger.error(f"Error getting apps: {e}")
        logger.debug(traceback.format_exc())

    return apps


async def execute_command(action: str, **kwargs):
    """Execute a command on Apple TV."""
    if not atv:
        logger.warning("Apple TV not connected, cannot execute command")
        return

    try:
        rc = atv.remote_control

        # Navigation commands
        if action == 'up':
            await rc.up()
        elif action == 'down':
            await rc.down()
        elif action == 'left':
            await rc.left()
        elif action == 'right':
            await rc.right()
        elif action == 'select':
            await rc.select()
        elif action == 'menu':
            await rc.menu()
        elif action == 'home':
            await rc.home()

        # Media commands
        elif action == 'play':
            await rc.play()
        elif action == 'pause':
            await rc.pause()
        elif action == 'play_pause':
            await rc.play_pause()
        elif action == 'stop':
            await rc.stop()
        elif action == 'next':
            await rc.next()
        elif action == 'previous':
            await rc.previous()

        # Power commands
        elif action == 'turn_on':
            if hasattr(atv, 'power') and atv.power:
                await atv.power.turn_on()
        elif action == 'turn_off':
            if hasattr(atv, 'power') and atv.power:
                await atv.power.turn_off()
        elif action == 'wakeup':
            if hasattr(atv, 'power') and atv.power:
                await atv.power.turn_on()
        elif action == 'suspend':
            if hasattr(atv, 'power') and atv.power:
                await atv.power.turn_off()

        # App launch
        elif action == 'launch_app':
            app_id = kwargs.get('app_id')
            if app_id and hasattr(atv, 'apps') and atv.apps:
                await atv.apps.launch_app(app_id)

        # Play URL
        elif action == 'play_url':
            url = kwargs.get('url')
            if url and hasattr(atv, 'stream') and atv.stream:
                await atv.stream.play_url(url)

        # Multiple commands
        elif action == 'multi':
            commands = kwargs.get('commands', [])
            for cmd in commands:
                await execute_command(cmd)
                await asyncio.sleep(0.3)  # Small delay between commands

        else:
            logger.warning(f"Unknown action: {action}")
            return

        logger.info(f"Executed command: {action}")

    except Exception as e:
        logger.error(f"Error executing command '{action}': {e}")
        logger.debug(traceback.format_exc())


# =============================================================================
# Async Tasks
# =============================================================================

async def task_state_update():
    """Task: Periodically update and publish playback state."""
    logger.info(f"State update task started (interval: {config.state_update_interval}s)")

    while not shutdown_event.is_set():
        try:
            state = await get_state()
            mqtt_publish(config.topic_state, json.dumps(state), retain=False)
        except Exception as e:
            logger.error(f"Error in state update task: {e}")
            logger.debug(traceback.format_exc())

        await asyncio.sleep(config.state_update_interval)


async def task_apps_update():
    """Task: Periodically update and publish apps list."""
    logger.info(f"Apps update task started (interval: {config.apps_update_interval}s)")

    while not shutdown_event.is_set():
        try:
            apps = await get_apps()
            mqtt_publish(config.topic_apps, json.dumps(apps), retain=True)
        except Exception as e:
            logger.error(f"Error in apps update task: {e}")
            logger.debug(traceback.format_exc())

        await asyncio.sleep(config.apps_update_interval)


async def task_command_handler():
    """Task: Handle incoming MQTT commands."""
    logger.info("Command handler task started")

    while not shutdown_event.is_set():
        try:
            # Wait for command with timeout to allow checking shutdown_event
            try:
                cmd = await asyncio.wait_for(command_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            topic = cmd['topic']
            payload = cmd['payload']

            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in command: {payload}")
                continue

            # Handle set commands
            if topic == config.topic_set:
                action = data.pop('action', None)
                if action:
                    await execute_command(action, **data)

            # Handle get commands
            elif topic == config.topic_get:
                get_type = data.get('type', 'all')

                if get_type in ('state', 'all'):
                    state = await get_state()
                    mqtt_publish(config.topic_state, json.dumps(state), retain=False)

                if get_type in ('apps', 'all'):
                    apps = await get_apps()
                    mqtt_publish(config.topic_apps, json.dumps(apps), retain=True)

                logger.info(f"Forced update: {get_type}")

        except Exception as e:
            logger.error(f"Error in command handler: {e}")
            logger.debug(traceback.format_exc())


# =============================================================================
# Main
# =============================================================================

async def main():
    """Main application entry point."""
    global config, logger, mqtt_client, shutdown_event, command_queue, main_loop

    # Load configuration
    config = Config()

    # Setup logging
    logger = setup_logging(config.log_level)
    logger.info("=" * 60)
    logger.info("Apple TV to MQTT Bridge starting...")
    logger.info("=" * 60)

    # Initialize event and queue
    shutdown_event = asyncio.Event()
    command_queue = asyncio.Queue()

    # Store main event loop for cross-thread access
    main_loop = asyncio.get_running_loop()

    # Setup signal handlers
    loop = main_loop

    def signal_handler(sig):
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    # Setup and connect MQTT
    mqtt_client = setup_mqtt()
    if not connect_mqtt(mqtt_client):
        logger.error("Could not connect to MQTT broker, exiting")
        return 1

    # Connect to Apple TV
    if not await connect_appletv():
        logger.error("Could not connect to Apple TV, exiting")
        mqtt_client.publish(config.topic_availability, "offline", qos=config.mqtt_qos, retain=True)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        return 1

    # Start tasks
    tasks = [
        asyncio.create_task(task_state_update()),
        asyncio.create_task(task_apps_update()),
        asyncio.create_task(task_command_handler()),
    ]

    logger.info("All tasks started, bridge is running")

    # Wait for shutdown
    await shutdown_event.wait()

    # Graceful shutdown
    logger.info("Shutting down...")

    # Cancel tasks
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    # Publish offline status
    mqtt_publish(config.topic_availability, "offline", retain=True)

    # Close Apple TV connection
    if atv:
        atv.close()
        logger.info("Apple TV connection closed")

    # Disconnect MQTT
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    logger.info("MQTT disconnected")

    logger.info("Shutdown complete")
    return 0


if __name__ == '__main__':
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
