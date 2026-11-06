"""Administrator route composition and the standalone development launcher."""


def register_admin_routes(app):
    from admin_routes import accounts, auth, history, knowledge, logs, models, pages, pdf

    for routes in (auth, accounts, pages, models, knowledge, pdf, logs, history):
        routes.register_routes(app)


def admin_run():
    from main import create_serving_apps
    from serving import serve_development
    from settings import ADMIN_PORT

    _, admin_app = create_serving_apps()
    serve_development(admin_app, ADMIN_PORT)


if __name__ == '__main__':
    admin_run()
