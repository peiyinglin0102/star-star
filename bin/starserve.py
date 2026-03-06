from gunicorn.app.wsgiapp import WSGIApplication

class App(WSGIApplication):
    def init(self, *args, **kwargs):
        return {
            "workers": 4,
            "bind": "127.0.0.1:5001",
            "errorlog": "/tmp/starstar/gunicorn.error.log",
            "pidfile": "/tmp/starstar/gunicorn.pid",
            "daemon": True,
            # 關鍵：把行程名稱改成 starstar，避免被 pkill -f gunicorn 命中
            "proc_name": "starstar",
        }

    def load(self):
        from services.app import app
        return app

if __name__ == "__main__":
    App().run()
