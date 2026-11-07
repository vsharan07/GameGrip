"""Serial CSV contract. No port is opened."""

from sensor.csv_stream import ArrivalRate, LineSource, StreamQuality, parse_csv_line, parse_stream_line


def test_valid_csv_is_accel_g_then_gyro_dps():
    sample = parse_csv_line("  0.10,-0.20,1.00,1.50,-2.25,3.00\n")
    assert sample == (0.10, -0.20, 1.00, 1.50, -2.25, 3.00)


def test_diagnostic_and_invalid_lines_are_not_samples():
    assert parse_csv_line("") is None
    assert parse_csv_line("no I2C devices found") is None
    assert parse_csv_line("I2C scan SDA=21 SCL=22") is None
    assert parse_csv_line("I2C device at 0x68") is None
    assert parse_csv_line("status imu=real samples=50 read_failures=0") is None
    assert parse_csv_line("status imu=missing") is None
    assert parse_csv_line("1,2,3,4,5") is None
    assert parse_csv_line("1,2,3,4,5,6,7") is None
    assert parse_csv_line("a,b,c,d,e,f") is None
    assert parse_csv_line(None) is None
    assert parse_csv_line("nan,0,1,0,0,0") is None


def test_optional_button_column_is_only_zero_or_one():
    sample, button = parse_stream_line("0,0,1,1,2,3,1")
    assert sample == (0.0, 0.0, 1.0, 1.0, 2.0, 3.0)
    assert button == 1
    assert parse_stream_line("0,0,1,1,2,3,0")[1] == 0
    assert parse_stream_line("0,0,1,1,2,3")[1] is None
    assert parse_csv_line("0,0,1,1,2,3,1") == (0.0, 0.0, 1.0, 1.0, 2.0, 3.0)


def test_empty_readline_is_a_missing_sample():
    class ClosedPort(object):
        def readline(self):
            return b""

    assert LineSource(ClosedPort()).read_sample() is None


def test_stream_goes_down_when_samples_stop():
    quality = StreamQuality(timeout_s=1.0)
    assert quality.is_down(0.0)
    quality.observe((0, 0, 1, 0, 0, 0), 0.0)
    quality.observe(None, 0.2)
    assert quality.rejected == 1
    assert quality.is_down(0.9) is False
    assert quality.is_down(1.0) is True


def test_arrival_rate_counts_the_last_second():
    rate = ArrivalRate(window_s=1.0)
    rate.add(0.0)
    rate.add(0.5)
    rate.add(1.2)
    assert rate.hz() == 2
