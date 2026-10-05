# Respaldos del Portal Mantenimiento Wintec

## Dónde están los datos
- **Base de datos:** PostgreSQL en Railway (servicio `Postgres` del proyecto, con su volumen `postgres-volume`).
  La app la usa mediante la variable `DATABASE_URL` del servicio `inventario-app`.
- **Fotos y documentos:** bucket S3/R2 (variables `S3_*`).

## Respaldo automático
- Todas las noches a las **3:00** la app genera un respaldo completo de la base
  (todas las tablas, incluidos turnos, avisos y la configuración del correo de ingreso)
  y lo sube **privado** al bucket, carpeta `backups-db/`, como
  `inventario_AAAAMMDD_HHMMSS.sql.gz`.
- Se guardan los últimos `BACKUPS_A_MANTENER` respaldos (variable en Railway; por defecto 14).
- Se pueden ver, generar a mano y descargar en **Administración → Sistema y respaldos**.

> Railway ofrece además respaldos propios de PostgreSQL y recuperación a un punto en el
> tiempo, pero solo en su plan Pro. Con el plan actual, el respaldo de la app es el que vale.

## Cómo restaurar (probado)
1. Descargar el respaldo desde **Sistema y respaldos** (archivo `.sql.gz`).
2. En Railway, crear una base PostgreSQL **nueva** (New → Database → PostgreSQL) y copiar su
   `DATABASE_URL` pública (pestaña *Connect* de esa base).
3. En el PC, en la carpeta del proyecto, con el entorno virtual activo:
   ```
   set DATABASE_URL=postgresql://...   (la URL de la base nueva)
   python restaurar_respaldo.py inventario_AAAAMMDD_HHMMSS.sql.gz
   ```
   El script crea las tablas, carga los datos, deja los contadores de id al día y muestra
   cuántas filas quedaron por tabla. Esa cantidad debe coincidir con los comentarios
   `-- tabla (N filas)` que trae el respaldo.
4. Revisar la base nueva y, si está bien, cambiar en `inventario-app` la variable
   `DATABASE_URL` para que apunte a ella (Railway vuelve a desplegar solo).

Si la base de destino ya tiene datos, el script se detiene. Para reemplazarlos a propósito:
`python restaurar_respaldo.py respaldo.sql.gz --reemplazar` (borra lo que haya).

## Revisión recomendada
- Una vez al mes: entrar a **Sistema y respaldos** y confirmar que el último respaldo es de esa madrugada.
- Cada 6 meses: hacer una restauración de prueba en una base nueva, siguiendo los pasos de arriba.
