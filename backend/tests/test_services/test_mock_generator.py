import pytest
from app.services.mock_data_generator import MockDataGenerator, PRESET_DEVICES

def test_preset_devices_exist():
    assert len(PRESET_DEVICES) > 0

def test_preset_devices_have_required_fields():
    for device in PRESET_DEVICES:
        assert "id" in device
        assert "device_code" in device
        assert "name" in device
        assert "device_type" in device
        assert "status" in device

def test_mock_generator_init():
    gen = MockDataGenerator()
    assert gen.sensor_interval > 0
    assert gen.alert_interval > 0
