# Slotmuse · Planificación de escuelas

Webapp para preparar temporadas formativas y horarios de una escuela: periodos de materias, grupos y subgrupos, espacios especializados, docentes nominales y reservas por asignar.

La versión 0.2 incorpora **escuelas independientes**, acceso de **superadministrador con usuario y contraseña** y una **guía de configuración inicial**. La Escuela Demo se conserva como entorno separado con datos ficticios y perfiles intercambiables.

## Ejecutar la plataforma

Python 3.9 o posterior, sin dependencias Python externas:

```sh
python3 school_app.py
```

Abrir http://127.0.0.1:8000. En el primer arranque local se crea un superadministrador y se guarda su contraseña aleatoria en `data/admin-inicial.txt`, con permisos de lectura limitados al usuario del proceso. No se muestra la contraseña en los logs. También se puede configurar de antemano mediante `ADMIN_USERNAME` y `ADMIN_PASSWORD` (mínimo 12 caracteres). Las variables solo se utilizan para crear el primer administrador; cambiarlas después no cambia una contraseña existente.

Para el contenedor local:

```sh
cp .env.example .env
# Configurar una contraseña única en .env.
docker compose up -d --build
```

El volumen `academia-data` conserva las escuelas y las sesiones. Cerrar o reiniciar el servicio no borra los datos. `docker compose down -v` sí elimina el volumen.

## Primer acceso y configuración

1. Iniciar sesión como superadministrador. La primera pantalla es la **Guía de configuración**.
2. **Crear escuela**: nombre, primer curso/temporada y fechas. Empieza vacía; no copia el catálogo de la demo.
3. **Espacios**: aulas de teoría, tatami, piscina o pista exterior y su aforo.
4. **Grupos y subgrupos**: alumnos por grupo y número de subgrupos técnicos. Se usa el tamaño del subgrupo más grande al comprobar aforos.
5. **Materias**: formato (grupo completo/subgrupo), tipo de espacio, fechas previstas de inicio y fin y bloques semanales de 90 minutos.
6. **Docentes y reservas**: habilitaciones, días disponibles y cargas máximas diaria/semanal.
7. **Temporada**: revisar el mapa de materias y crear borradores semanales.
8. **Semana**: ajustar sesiones, fijarlas, completar reservas docentes y publicar para su consulta.

La guía muestra el progreso y los conceptos que faltan. Permite añadir y editar el catálogo. Los cambios que invalidarían sesiones existentes se rechazan hasta ajustar esas sesiones. La generación se bloquea si faltan grupos, espacios, materias, docentes, habilitaciones o aforos compatibles.

El selector de escuela permite volver a la demo o continuar configurando otra escuela. La versión actual contempla un curso/temporada por escuela. La gestión de múltiples cursos históricos y de cuentas de personal de las escuelas reales queda para la siguiente versión.

## Escuela Demo

Se puede entrar desde **Probar la Escuela Demo**, sin contraseña. Incluye 96 alumnos en dos grupos de 48, cuatro subgrupos de 24, cinco espacios y cinco materias. El selector cambia entre gestora, coordinador y dos alumnos ficticios.

Solo la demo admite cambiar de identidad mediante el selector. En una escuela real, ese encabezado se ignora y el servidor exige una sesión autenticada. Los visitantes de la demo no pueden listar ni acceder a las escuelas reales.

Si existe `data/academia.db` de la versión anterior, el primer arranque de la plataforma hace una copia consistente para la Escuela Demo. Conserva el archivo original. Las escuelas creadas después empiezan sin datos de ejemplo.

## Modelo y persistencia

```text
data/
  platform.db          # Superadministrador, sesiones y registro de escuelas
  schools/
    demo.db            # Escuela Demo
    <id-escuela>.db    # Catálogo y semanas de una escuela independiente
```

La selección de base de datos se limita al contexto de cada petición; las peticiones concurrentes no comparten una escuela seleccionada globalmente. El identificador de escuela se valida en el servidor. El superadministrador tiene acceso a todas las escuelas de esta instalación.

Las contraseñas usan PBKDF2-HMAC-SHA256 con sal aleatoria y 600.000 iteraciones. Las sesiones duran 12 horas: token aleatorio, hash en la base de datos, cookie HttpOnly y SameSite=Strict. En Railway la cookie es Secure. Las mutaciones autenticadas requieren token CSRF; se rechazan orígenes cruzados. Tras cinco fallos de acceso se bloquea durante 15 minutos la combinación de usuario e IP de conexión. El cierre de sesión invalida la sesión en el servidor.

No hay recuperación de contraseña ni administración de usuarios en la interfaz de esta versión. Antes de un uso operativo se deben añadir estos flujos, auditoría, políticas de respaldo y las cuentas/permisos del personal real.

## Planificación

- Cada sesión ocupa un espacio, un docente y una franja de 90 minutos.
- Una clase de grupo completo bloquea a todos sus subgrupos. Dos subgrupos pueden trabajar en paralelo con recursos distintos.
- Se comprueban aforo, tipología, habilitación, disponibilidad, carga y cruces de alumnado/docente/espacio.
- Cada reserva representa un puesto individual que no puede cubrir dos sesiones simultáneas.
- La heurística prioriza espacios técnicos escasos y busca repartir carga, evitar repeticiones diarias y favorecer docentes nominales y franjas tempranas. Puede hacer hasta 40 intentos cuando faltan sesiones. **No garantiza un óptimo global.**
- Solo se generan sesiones dentro de las fechas de cada asignatura. El objetivo completo se exige también en semanas parciales; si no cabe, el borrador queda incompleto y no se publica.
- La temporada delimita semanas. Todavía no descuenta festivos, vacaciones o días no lectivos.
- La carga prevista suma objetivos semanales, no horas ya impartidas ni una bolsa total obligatoria.
- Los borradores están separados de los horarios publicados. Publicar exige cobertura completa, ausencia de conflictos y todos los docentes nominales.
- La generación de temporada solo crea semanas que faltan. Al regenerar una semana se conservan las sesiones fijadas.
- Cambiar periodos mediante el editor semanal retira sesiones fuera de periodo de todos los borradores, incluidas las fijadas, pero conserva las versiones publicadas.

## Railway

`railway.json` utiliza el Dockerfile, una réplica y `/api/health`, y exige un volumen montado en `/app/data`.

Configurar en el servicio:

```text
HOST=0.0.0.0
PORT=8000
DATABASE_PATH=/app/data/academia.db
APP_DATA_DIR=/app/data
ADMIN_USERNAME=<usuario del superadministrador>
ADMIN_PASSWORD=<contraseña única de al menos 12 caracteres>
COOKIE_SECURE=1
RAILWAY_RUN_UID=0
PYTHONUNBUFFERED=1
```

`RAILWAY_RUN_UID=0` permite escribir en el volumen montado con propietario root, conforme a la [documentación de Railway](https://docs.railway.com/volumes). Mantener una sola réplica con este almacenamiento SQLite. Configurar un volumen pequeño, una región y dominio HTTPS de Railway.

En Railway no se genera una contraseña automática: el primer arranque exige `ADMIN_PASSWORD`. Las credenciales, `.env`, las bases de datos y la distribución Windows no se publican ni se copian en la imagen.

La aplicación sigue siendo un prototipo con el servidor HTTP de la biblioteca estándar. El alojamiento compartido de prueba necesita HTTPS y copias del volumen; una instalación operativa requiere revisar el servidor de aplicación, recuperación de credenciales y permisos de usuarios reales.

## Copias de seguridad

Con el servicio detenido, copiar **todo** el directorio de datos, incluidas `platform.db` y `schools/`. Restaurarlos juntos conserva las relaciones entre escuelas y sus archivos. No copiar solo la base de datos de una escuela sin su registro central.

## Validación

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/platform.js
```

Las pruebas abren puertos locales temporales y bases de datos independientes. Cubren restricciones, periodos, publicación, revisiones, autenticación, CSRF, aislamiento de escuelas y arranque del paquete Windows cuando el ZIP está disponible.

## Windows portátil anterior

El ZIP `Academia-demo-Windows11-x64-v0.1.zip` ya generado es una instantánea de la demo 0.1 y permanece independiente de esta plataforma. Incluye Python y se abre extrayendo el ZIP y ejecutando `INICIAR.cmd`. No necesita Docker ni instalación de Python. Usa datos ficticios y guarda sus cambios en la carpeta extraída.

`packaging/build_windows.py` permite reconstruir la distribución demo usando el runtime embebido oficial de Python. Esa distribución usa el servidor demo `server.py`, sin la plataforma multi-escuela `school_app.py`. No incluye credenciales ni datos reales.

## Fundamentos

Se adopta la separación entre restricciones obligatorias y preferencias de la literatura de planificación académica. El aforo se considera obligatorio, una adaptación más estricta que su penalización blanda en ITC 2007.

- Di Gaspero, McCollum y Schaerf, [Curriculum-based Course Timetabling (ITC 2007)](https://www.eeecs.qub.ac.uk/itc2007/curriculmcourse/report/curriculumtechreport.pdf).
- [Modelo y evaluación ITC 2007](https://www.eeecs.qub.ac.uk/itc2007/curriculmcourse/course_curriculm_index_files/evaluation.htm).
- [UniTime: componentes teóricos y prácticos](https://help.unitime.org/manuals/student-scheduling).
- [UniTime: asignación y disponibilidad de docentes](https://help.unitime.org/manuals/instructor-scheduling).

## Próximas iteraciones

Múltiples cursos por escuela, cuentas para docentes/coordinadores/alumnado, tipos de espacio personalizados, festivos, horas totales, duraciones variables, desplazamientos, eliminación/archivo de conceptos con referencias, importaciones y optimización CP-SAT.

## Licencia

[MIT](LICENSE). El runtime opcional de Python para Windows conserva su licencia
original; no se incluye en el repositorio. La literatura y la documentación
referenciadas conservan sus respectivas licencias.
