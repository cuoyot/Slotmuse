# Slotmuse · Planificación de escuelas

Webapp para preparar temporadas formativas y horarios de una escuela: periodos de materias, grupos y subgrupos, espacios especializados, docentes nominales y reservas por asignar.

Slotmuse incorpora **escuelas independientes**, acceso con **cuentas y roles por escuela** y una **guía de configuración inicial**. La Escuela Demo se conserva como entorno separado con datos ficticios y perfiles intercambiables.

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
5. **Materias**: formato (grupo completo/subgrupo), tipo de espacio, fechas previstas de inicio y fin y horas totales, horas semanales y duración habitual de sesión.
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
  platform.db          # Usuarios, roles por escuela, sesiones y escuelas
  schools/
    demo.db            # Escuela Demo
    <id-escuela>.db    # Catálogo y semanas de una escuela independiente
```

La selección de base de datos se limita al contexto de cada petición; las peticiones concurrentes no comparten una escuela seleccionada globalmente. El identificador de escuela se valida en el servidor. El superadministrador tiene acceso a todas las escuelas de esta instalación.

Las contraseñas usan PBKDF2-HMAC-SHA256 con sal aleatoria y 600.000 iteraciones. Las sesiones duran 12 horas: token aleatorio, hash en la base de datos, cookie HttpOnly y SameSite=Strict. En Railway la cookie es Secure. Las mutaciones autenticadas requieren token CSRF; se rechazan orígenes cruzados. Tras cinco fallos de acceso se bloquea durante 15 minutos la combinación de usuario e IP de conexión. El cierre de sesión invalida la sesión en el servidor.

La interfaz permite crear usuarios, editar roles y desactivar su acceso por escuela. La recuperación y el cambio de contraseña todavía no tienen interfaz; quedan pendientes junto con auditoría completa y políticas de respaldo.

## Planificación

- Cada sesión ocupa un espacio y un docente durante su duración real, en una cuadrícula de 30 minutos.
- Una clase de grupo completo bloquea a todos sus subgrupos. Dos subgrupos pueden trabajar en paralelo con recursos distintos.
- Se comprueban aforo, tipología, habilitación, disponibilidad, carga y cruces de alumnado/docente/espacio.
- Cada reserva representa un puesto individual que no puede cubrir dos sesiones simultáneas.
- La heurística prioriza espacios técnicos escasos y busca repartir carga, evitar repeticiones diarias y favorecer docentes nominales y franjas tempranas. Puede hacer hasta 40 intentos cuando faltan sesiones. **No garantiza un óptimo global.**
- Solo se generan sesiones dentro de las fechas de cada asignatura. La carga semanal se aplica hasta completar las horas totales por alumno; la última semana puede tener menos sesiones. Si las horas no caben entre inicio y fin, se bloquea la generación y se pide ajustar el periodo o la carga.
- La temporada delimita semanas. Todavía no descuenta festivos, vacaciones o días no lectivos.
- Cada materia tiene horas totales y horas semanales por alumno, en múltiplos de 0,5 horas. La carga prevista muestra las horas totales, no horas ya impartidas. Las materias existentes conservan su carga previa como total inicial.
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
node --check static/i18n.js
node tests/test_i18n.cjs
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

Múltiples cursos por escuela, cuentas para docentes/coordinadores/alumnado, tipos de espacio personalizados, festivos, duraciones variables, desplazamientos, eliminación/archivo de conceptos con referencias, importaciones y optimización CP-SAT.

## Licencia

[MIT](LICENSE). El runtime opcional de Python para Windows conserva su licencia
original; no se incluye en el repositorio. La literatura y la documentación
referenciadas conservan sus respectivas licencias.

## Idiomas

La interfaz está disponible en español y catalán. El botón circular con la bandera
del idioma activo está en el acceso y en la cabecera de la aplicación. La elección
se recuerda en el navegador; sin preferencia guardada, se usa catalán cuando el
idioma del navegador es catalán y español en los demás casos. El cambio no recarga
la página ni borra lo escrito en el formulario de acceso.

`static/ca.json` contiene el catálogo catalán; `static/i18n.js` aplica las traducciones
a los textos de interfaz y formatea las fechas mediante `Intl`. Los segmentos
literales de las plantillas están separados de sus valores dinámicos: los nombres
de escuelas, docentes, materias y otros datos introducidos por el usuario no se
traducen ni se modifican en la base de datos.

### Cuentas y roles por escuela

El acceso es único para todas las cuentas. El superadministrador crea escuelas; desde **Usuarios**, el superadministrador o un administrador de escuela crea cuentas con contraseña inicial (mínimo 12 caracteres), nombre, rol y acceso activo/desactivado.

- **Alumno/a**: vinculado a un subgrupo; consulta únicamente sus sesiones publicadas, incluidas las clases de su grupo completo.
- **Profesor/a**: vinculado a un docente nominal; consulta únicamente su agenda publicada.
- **Coordinador/a**: consulta la planificación y las tareas calculadas a partir de semanas incompletas, reservas pendientes y publicaciones; puede asignar docentes, pero no publicar ni cambiar el catálogo.
- **Administrador/a de escuela**: configura y planifica su escuela, publica horarios y gestiona sus usuarios. No puede crear escuelas ni conceder el rol global de superadministrador.

Los permisos se comprueban en cada petición del servidor. Las respuestas personales excluyen borradores, actividad y agendas ajenas. Los cambios de rol y la desactivación se aplican también a las sesiones abiertas. La migración añade las tablas y campos necesarios conservando escuelas, horarios y la cuenta inicial. La Escuela Demo mantiene el selector de perfiles ficticios sin contraseña.

### Disponibilidad de espacios y reajuste

Desde **Espacios y docentes → Disponibilidad**, define franjas semanales por día con una fecha de entrada en vigor. Ejemplo: piscina, lunes y miércoles, 08:00–13:00. Se admite más de una franja por día; las franjas contiguas se unen. Sin franjas, el espacio queda cerrado. Antes de la primera regla se conserva la disponibilidad completa anterior, y cada nueva fecha sustituye las reglas semanales desde ese día hasta el siguiente cambio.

Las sesiones deben caber enteras dentro de una franja: con cierre a las 13:00 no se puede usar una sesión que termine después de las 13:00. La validación se aplica a generación, edición manual, asignaciones y publicación.

**Ver impacto** muestra semanas y sesiones afectadas, incluidas las fijadas y las publicadas. **Aplicar disponibilidad** puede reajustar los borradores en una sola transacción: conserva las sesiones válidas y busca huecos para el resto. Las fijadas incompatibles se recolocan tras mostrarse en la vista previa. Si no hay solución completa, guarda una propuesta parcial y bloquea su publicación hasta completar la cobertura. Las versiones publicadas se conservan y aparecen avisos de revisión para coordinación y en las agendas personales afectadas; hay que revisar y publicar de nuevo. La comprobación de versión evita aplicar una vista previa sobre un catálogo que haya cambiado.

### Altas por filas

Las altas de espacios, grupos, materias, docentes y alumnado/usuarios utilizan una tabla. **Tab** avanza entre campos, **Enter** pasa a la misma columna de la siguiente fila y **Ctrl/⌘ + Enter** guarda. Se pueden añadir filas sin cerrar la tabla. Las altas del catálogo se guardan juntas de forma atómica; las cuentas se guardan por fila y las guardadas quedan identificadas para no repetirlas si otra falla. Los errores conservan el contenido introducido. Los docentes usan los números de las materias que se muestran encima de la tabla y días 1–5 (lunes–viernes).

### Ubicaciones, duración y desdoblamientos

Los espacios eligen su ubicación de una lista por escuela, ampliable en **Ubicaciones en filas**. Las ubicaciones distintas requieren al menos **30 minutos** entre sesiones del mismo docente o de alumnado compartido; se comparan intervalos reales, también al editar y publicar.

La cuadrícula va de 08:00 a 15:00 en intervalos de 30 minutos. Las nuevas materias usan normalmente 60 minutos por sesión, configurables en múltiplos de 30. Las horas totales y semanales admiten incrementos de 0,5 h. La última sesión puede acortarse a los minutos pendientes. La edición manual permite una duración distinta por sesión; la cobertura se calcula en minutos, sin contar como equivalentes sesiones de diferente duración. Los horarios históricos conservan sus horas exactas y sus sesiones de 90 minutos, incluidas las publicadas a las 09:45.

El generador agrupa actividades de subgrupo de igual duración, con materias distintas y espacios compatibles en una ubicación común, para ocupar simultáneamente todos los subgrupos del grupo. Por ejemplo A1 natación/A2 atletismo y posteriormente A1 atletismo/A2 natación. Cada bloque guarda un identificador compartido; se muestra como **Desdoblamiento**, con detalle por subgrupo, actividad, espacio y docente. No se mezclan grupos diferentes. Si un cambio de disponibilidad afecta a una parte, se reajusta todo el bloque; al mover manualmente una sesión se mueven sus compañeras. La publicación valida su integridad y coincidencia de día, horario y ubicación.
