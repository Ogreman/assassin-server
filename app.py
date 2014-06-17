import os
from datetime import datetime
from functools import wraps

from flask import request, url_for, render_template, current_app, redirect

from flask.ext.api import FlaskAPI, status, exceptions
from flask.ext.api.decorators import set_renderers
from flask.ext.api.renderers import HTMLRenderer
from flask.ext.api.exceptions import APIException
from flask.ext.sqlalchemy import SQLAlchemy

from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, String
from unipath import Path


TEMPLATE_DIR = Path(__file__).ancestor(1).child("templates")

app = FlaskAPI(__name__, template_folder=TEMPLATE_DIR)
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', 'sqlite:///dev.db'),
)
db = SQLAlchemy(app)


AMOUNT = 5


def game_active_required(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if current_app.game_active:
            return func(*args, **kwargs)
        else:
            return redirect(url_for('game_status'))
        return func(*args, **kwargs)
    return decorated_view


class User(db.Model):

    __tablename__ = "user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    has_pot = Column(Boolean, default=False)
    created = Column(DateTime)
    email = Column(String)
    alive = Column(Boolean, default=True)
    score = Column(Integer, default=0)
    contributions = db.relationship('Contribution', backref='user', lazy='select')

    def __repr__(self):
        return """Player #{user}
        contributed: {amount}
        score: {score}
        does {have} the pot.
        """.format(
            user=self.id,
            score=self.score,
            have="have" if self.has_pot else "not have",
            amount=len(self.contributions) * AMOUNT
        )

    def to_json(self):
        return {
            'id': self.id,
            'alive': self.alive,
            'pot': self.has_pot,
            'contributions': len(self.contributions),
            'email': self.email,
            'created': self.created,
            'score': self.score,
        }

    @classmethod
    def get_users(self):
        return [
            user.to_json() for user in User.query.all()
        ]

    @classmethod
    def get_others(self, id):
        return [
            user.to_json() for user in User.query.filter(User.id != id)
        ]


class Contribution(db.Model):

    __tablename__ = "contribution"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    amount = Column(Integer)
    created = Column(DateTime)
    paid = Column(Boolean, default=False)

    def __repr__(self):
        return "Contribution of {amount} from #{user} at {dt} ({paid})".format(
            amount=self.amount,
            user=self.user.id,
            dt=self.created,
            paid="paid" if self.paid else "not paid"
        )

    def to_json(self):
        return {
            'id': self.id,
            'amount': self.amount,
            'created': self.created,
            'user': self.user_id,
            'paid': self.paid,
        }

    def pay(self):
        if not self.paid:
            self.user.score = self.user.score - self.amount
            db.session.add(self.user)
            self.paid = True
            print "Contribution {id} paid successfully".format(id=self.id)
            return True
        else:
            print "Contribution {id} already paid".format(id=self.id)
            return False

    @classmethod
    def get_contributions(self):
        return [
            cont.to_json() for cont in Contribution.query.all()
        ]

    @classmethod
    def pay_contributions(self):
        user = User.query.filter_by(has_pot=True).first()
        for contribution in Contribution.query.filter_by(paid=False):
            if contribution.pay():
                user.score = user.score + contribution.amount
                db.session.add(contribution)
        db.session.add(user)
        db.session.commit()


@app.route("/api/contributions/", methods=['GET', 'POST'])
@game_active_required
def contributions():
    """
    List or create contributions.
    """
    if request.method == 'POST':
        if request.data.get('user'):
            contribution = Contribution(
                user_id=request.data.get('user'),
                created=datetime.now(),
                amount=AMOUNT,
            )
            db.session.add(contribution)
            db.session.commit()
            return contribution.to_json(), status.HTTP_201_CREATED
        return {'error': 404 }, status.HTTP_404_NOT_FOUND
    return Contribution.get_contributions(), status.HTTP_200_OK


@app.route("/api/contributions/<id>/")
@game_active_required
def contribution(id):
    try:
        return Contribution.query.filter_by(id=id).first().to_json(), status.HTTP_200_OK
    except AttributeError:
        return { 'error': 404 }, status.HTTP_404_NOT_FOUND


@app.route("/api/users/", methods=['GET', 'POST'])
def users():
    """
    List or create users.
    """
    if request.method == 'POST':
        try:
            user = User.query.filter_by(email=request.data['email']).first()
            if user is None:
                user = User(
                    created=datetime.now(),
                    has_pot=False if User.query.all() else True,
                    email=request.data['email'],
                )
                db.session.add(user)
                db.session.commit()
            return user.to_json(), status.HTTP_201_CREATED
        except KeyError:
            return { 'error': 400 }, status.HTTP_400_BAD_REQUEST
    return User.get_users(), status.HTTP_200_OK


@app.route("/api/targets/<id>/")
@game_active_required
def targets(id):
    return User.get_others(id), status.HTTP_200_OK


@app.route("/api/users/<id>/")
def user(id):
    try:
        return User.query.filter_by(id=id).first().to_json(), status.HTTP_200_OK
    except AttributeError:
        return { 'error': 404 }, status.HTTP_404_NOT_FOUND


@app.route("/api/hit/", methods=['POST'])
@game_active_required
def hit():

    user = User.query.get(request.data.get('id'))
    target = User.query.get(request.data.get('target'))

    if user and target:
        contribution = Contribution(
            user_id=user.id,
            created=datetime.now(),
            amount=AMOUNT,
        )
        db.session.add(contribution)

        if target.has_pot:
            target.has_pot = False
            user.has_pot = True
            db.session.add(target)
            db.session.add(user)

        db.session.commit()
        return { 'hit': user.has_pot }, status.HTTP_200_OK

    return { 'error': 404 }, status.HTTP_404_NOT_FOUND


@app.route("/api/end/<key>")
def end(key):
    if key == current_app.admin_key:
        Contribution.pay_contributions()
        current_app.game_active = False
        return { 'paid': True, 'status': False }, status.HTTP_200_OK
    else:
        return { 'paid': False, 'status': current_app.game_active }, status.HTTP_400_BAD_REQUEST


@app.route("/api/start/<key>")
def start(key):
    if key == current_app.admin_key:
        current_app.game_active = True
        return { 'status': True }, status.HTTP_200_OK
    else:
        return { 'status': current_app.game_active }, status.HTTP_400_BAD_REQUEST


@app.route("/api/status/")
def game_status():
    if current_app.game_active:
        return { 'status': current_app.game_active }, status.HTTP_200_OK
    else:
        return { 'status': current_app.game_active }, status.HTTP_503_SERVICE_UNAVAILABLE


if __name__ == "__main__":
    app.admin_key = os.environ.get('ADMIN_KEY', 'password')
    app.game_active = False
    app.run(debug=True, port=5001)