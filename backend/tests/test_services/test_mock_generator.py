from app.services.mock_data_generator import PRESET_DEVICES, MockDataGenerator


def test_preset_devices_exist():
    assert len(PRESET_DEVICES) > 0


def test_preset_devices_have_required_fields():
    for device in PRESET_DEVICES:
        assert "id" in device
        assert "code" in device
        assert "name" in device
        assert "lat" in device
        assert "lng" in device
        assert "district" in device


def test_mock_generator_init():
    gen = MockDataGenerator()
    assert gen.sensor_interval > 0
    assert gen.alert_interval_seconds > 0
