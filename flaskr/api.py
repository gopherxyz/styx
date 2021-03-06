"""Модуль **api** содержит ресурсы API приложения. Ресурсы реализованы
 при помощи класса :class:`flask_restx.Resource`. В экземплярах данных
 классов содержиться логика для обработки запросов к ресурсу.
 """
from flask import Blueprint
from flask_restx import Api
from flask_restx import Resource
from flaskr.models import User
from flaskr.models import Project
from flask_restx import fields
from flask_restx import reqparse
from flask_restx import marshal
from functools import wraps
from flaskr.models import db
from flask import request
from flask import url_for
from sqlalchemy.exc import IntegrityError

#: **api_bp** - это Blueprint, который содержит представления ресурсов API приложения.
#:
#: В дальнейшем api_bp используется классом :class:`flask_restx.Api`.
#:
#: Для данного Blueprint'a статическая директория и директория для 
#: шаблонов указаны корневеные (./static и ./templates).
api_bp = Blueprint('api', __name__, static_folder='static',
        template_folder='templates', url_prefix='/api')

#: Словарь **authorizations** содержит описание заголовков, которые 
#: используются для аутентификации запросов.
#:
#: *APITokenHeader* - заголовок, в котором хранится веб-токен
authorizations = {
        'APITokenHeader': {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-API-TOKEN'
        }
}

#: Главный объект API, который используется в дальнейшем для создания 
#: ресурсов при помощи класса :class:`flask_restx.Resource`.
api = Api(api_bp, authorizations=authorizations)

#: Модель пользователя, которая используется для форматирования ответов 
#: ресурсов.
#:
#: :Поля модели:
#:    * id (*int*) - идентификатор пользователя
#:    * username (*str*) - имя пользователя
#:    * email (*str*) - email пользователя
#:    * registration_time (:class:`datetime.datetime.DateTime`) - дата регистрации пользователя
#:    * self_url (*str*) - ссылка на самого себя
user_model = api.model('User', {
    'id': fields.Integer,
    'username': fields.String,
    'email': fields.String,
    'registration_time': fields.DateTime(dt_format='rfc822'),
    'self_url': fields.Url('api.user_settings_resource')
})

#: Модель проекта, которая используется для форматирования ответов 
#: ресурсов.
#:
#: :Поля модели:
#:    * id (*int*) - идентификатор проекта
#:    * user (:class:`flask_restx.fields.Nested`) - вложенный объект пользователя
#:    * project_name (*str*) - название проекта
#:    * self_url (*str*) - ссылка на самого себя
project_model = api.model('Project', {
    'id': fields.Integer,
    'user': fields.Nested(user_model),
    'project_name': fields.String,
    'self_url': fields.String(attribute=lambda x: url_for('api.project_root_resource', username=x.user.username, project_name=x.project_name))
})

def token_required(f):
    """Декоратор, который проверяет валидность веб-токена перед 
    выполнением запроса.

    :param *func* f: функция обработки запроса
    :returns: декоратор с переданной функцией
    :rtype: *func*
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        header_name = authorizations['APITokenHeader']['name'] 

        if header_name in request.headers:
            token = request.headers[header_name]

        if not token:
            return {'message': 'Необходим токен для выполнения операции.'}, 401

        is_success, result = User.decode_auth_token(token) 
        if is_success and result == kwargs['username']:
            return f(*args, **kwargs)
        elif is_success:
            return {'message': 'Необходим токен владельца проекта.'}, 401
        else:
            return {'message': result}, 401

    return decorated


@api.route('/<string:username>/settings')
@api.doc(params={'username': 'Имя пользователя'})
class UserSettings(Resource):
    """Ресурс настроек пользователя, URL ресурса: /{username}/settings.

    :Параметры запроса:
       * *username* - имя пользователя
    """
    @api.marshal_with(user_model, envelope='user')
    def get(self, username):
        """Обратывает GET запрос, возвращет представление пользователя с 
        использованием модели :data:`user_model`.
        """
        user = User.query.filter_by(username=username).first()
        return user


@api.route('/<string:username>/<string:project_name>')
class ProjectRoot(Resource):
    """Ресурс корня проекта, URL ресурса: /{username}/{project_name}.

    :Параметры запроса:
       * *username* - имя пользователя
       * *project_name* - название проекта
    """
    parser = reqparse.RequestParser()
    parser.add_argument('description', 
                help='Описание проекта', location='json')

    def get(self, username, project_name):
        """Обрабатывает GET запрос, возвращает представление проекта с 
        использованием модели :data:`project_model`. Либо ошибку 404 в случае, если указазно не верное название проекта.
        """
        user = User.query.filter_by(username=username).first()
        project = Project.query.filter_by(user_id=user.id, project_name=project_name).first()

        if not project:
            return {'message': 'Проекта с указанным названием не существует.'}, 404

        return marshal(project, project_model), 200

    @api.doc(security='APITokenHeader')
    @api.expect(parser)
    @token_required
    def post(self, username, project_name):
        """Обрабатывает POST запрос, добавляет проект в БД с указанными 
        параметрами, также возвращает представление проекта с 
        использованием модели :data:`project_model`. 

        В случае, если проект с таким названием уже существует, то 
        возвращает ошибку 422. 

        :param str description: описание проекта, передается в теле запроса
        """
        args = ProjectRoot.parser.parse_args()
        user = User.query.filter_by(username=username).first()

        project = Project(user_id=user.id,
                project_name=project_name,
                description=args['description'])
        db.session.add(project)

        try:
            db.session.commit()
        except IntegrityError:
            return {'message': 'Проект с таким названием уже существует.'}, 422

        return marshal(project, project_model), 200


@api.route('/<string:username>/<string:project_name>/webhook/github')
class Webhook(Resource):
    """Ресурс Github веб-хука проекта, URL ресурса: 
    /{username}/{project_name}/webhook/github.

    :Параметры запроса:
       * *username* - имя пользователя
       * *project_name* - название проекта
    """
    def get(self, username, project_name):
        """Обрабатывает GET запрос, возвращает представление с описанием  
        веб-хука и ссылкой на самого себя.

        :Поля представления:
           * *description* (*str*) - описание веб-хука
           * *self_url* (*str*) - ссылка на самого себя
        """
        user = User.query.filter_by(username=username).first()
        project = Project.query.filter_by(user_id=user.id, project_name=project_name).first()

        if not project:
            return {'message': 'Проекта с указанным названием не существует.'}, 404

        return {'description': 'Данный веб-хук подключается к сервису Github. После подключения, сервис будет отправлять POST запросы на данный ресурс для синхронизации репозитория с данным проектом. Если вы еще подключили данный веб-хук к репозиторию, то вам необходимо сделать, чтобы получить метрики вашего проекта.', 'self_url': '/{username}/{project_name}/webhook/github'.format(username=username, project_name=project_name)}, 200


api.add_resource(UserSettings, '/<string:username>/settings', endpoint='user_settings_resource')
api.add_resource(ProjectRoot, '/<string:username>/<string:project_name>', endpoint='project_root_resource')

