import os
import time
from flask import Flask, request
from flask_restful import Api, Resource
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from dateutil import parser
import pytz
from sqlalchemy.exc import OperationalError

app = Flask(__name__)


# Elastic APM
from elasticapm.contrib.flask import ElasticAPM

# Jaeger
from jaeger_client import Config
from flask_opentracing import FlaskTracing

# OpenTelemetry for SigNoz
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

app = Flask(__name__)

# Elastic APM configuration
app.config['ELASTIC_APM'] = {
    'SERVICE_NAME': 'my-web-app',
    'SECRET_TOKEN': 'your-secret-token',
    'SERVER_URL': 'http://apm-server:8200',
}

# Elastic APM setup
apm = ElasticAPM(app)


# Jaeger Tracing Configuration
def init_jaeger_tracer(service_name):
    config = Config(
        config={
            'sampler': {'type': 'const', 'param': 1},
            'local_agent': {'reporting_host': 'jaeger'},
            'logging': True,
        },
        service_name=service_name,
        validate=True,
    )
    return config.initialize_tracer()


# Initialize Jaeger tracer
jaeger_tracer = init_jaeger_tracer('my-web-app')
tracing = FlaskTracing(jaeger_tracer, True, app)

# OpenTelemetry for SigNoz
trace.set_tracer_provider(TracerProvider())
signoz_span_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://signoz-otel-collector:4317"))
trace.get_tracer_provider().add_span_processor(signoz_span_processor)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
api = Api(app)


class DeviceData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_name = db.Column(db.String(100), nullable=False)
    record_capture_time = db.Column(db.DateTime(timezone=True), nullable=False)
    sensor_1_data = db.Column(db.Float)
    sensor_2_data = db.Column(db.Float)

    def to_dict(self):
        return {
            'id': self.id,
            'device_name': self.device_name,
            'record_capture_time': self.record_capture_time.isoformat(),
            'sensor_1_data': self.sensor_1_data,
            'sensor_2_data': self.sensor_2_data
        }


class DeviceDataResource(Resource):
    def get(self, device_id=None):
        if device_id:
            device = DeviceData.query.get_or_404(device_id)
            return device.to_dict()
        else:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')

            if start_date and end_date:
                start = parser.isoparse(start_date)
                end = parser.isoparse(end_date)
                devices = DeviceData.query.filter(DeviceData.record_capture_time.between(start, end)).all()
            else:
                devices = DeviceData.query.all()

            return [device.to_dict() for device in devices]

    def post(self):
        data = request.json
        record_time = parser.isoparse(data['record_capture_time'])

        new_device = DeviceData(
            device_name=data['device_name'],
            record_capture_time=record_time,
            sensor_1_data=data.get('sensor_1_data'),
            sensor_2_data=data.get('sensor_2_data')
        )
        db.session.add(new_device)
        db.session.commit()
        return new_device.to_dict(), 201

    def put(self, device_id):
        device = DeviceData.query.get_or_404(device_id)
        data = request.json

        device.device_name = data.get('device_name', device.device_name)
        if 'record_capture_time' in data:
            device.record_capture_time = parser.isoparse(data['record_capture_time'])
        device.sensor_1_data = data.get('sensor_1_data', device.sensor_1_data)
        device.sensor_2_data = data.get('sensor_2_data', device.sensor_2_data)

        db.session.commit()
        return device.to_dict()

    def delete(self, device_id):
        device = DeviceData.query.get_or_404(device_id)
        db.session.delete(device)
        db.session.commit()
        return '', 204


api.add_resource(DeviceDataResource, '/device-data', '/device-data/<int:device_id>')


def wait_for_db(max_retries=5, delay=5):
    retries = 0
    while retries < max_retries:
        try:
            db.engine.connect()
            print("Successfully connected to the database!")
            return
        except OperationalError:
            retries += 1
            print(f"Database connection attempt {retries}/{max_retries} failed. Retrying in {delay} seconds...")
            time.sleep(delay)
    raise Exception("Could not connect to the database after multiple attempts")


if __name__ == '__main__':
    wait_for_db()
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', debug=True)