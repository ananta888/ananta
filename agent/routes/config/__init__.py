from flask import Blueprint
from agent.routes.config.benchmarks import benchmarks_bp
from agent.routes.config.templates import templates_bp
from agent.routes.config.teams import teams_bp
from agent.routes.config.core import core_bp

def register_config_blueprints(app):
    app.register_blueprint(benchmarks_bp, url_prefix="/api/config")
    app.register_blueprint(templates_bp, url_prefix="/api/config")
    app.register_blueprint(teams_bp, url_prefix="/api/config")
    app.register_blueprint(core_bp, url_prefix="/api/config")
