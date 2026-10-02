-- =============================================================================
-- AMATISTA · 004 · USUARIO DE APLICACIÓN (OPCIONAL, T-004)
-- (Oracle Autonomous Database ATP 26ai)
--
-- Crea AMATISTA_APP para que el backend NO se conecte como ADMIN. Si alguien
-- obtiene backend/.env, solo puede leer y escribir filas de las 8 tablas de
-- Amatista: no puede borrar tablas, crear usuarios ni tocar otros esquemas.
--
-- Permisos mínimos:
--   - CREATE SESSION (conectarse).
--   - SELECT, INSERT, UPDATE, DELETE sobre USUARIOS, SESIONES,
--     PROGRESO_LECCIONES, LOGROS, EVENTOS_APRENDIZAJE, CURSOS, MODULOS y
--     LECCIONES del esquema dueño (el usuario que ejecuta este script).
--   Las tablas siguen siendo del dueño (ADMIN). La purga con particiones
--   (job de 003) corre como dueño; la API solo necesita DELETE.
--
-- Cómo ejecutarlo:
--   1. Database Actions > SQL como ADMIN (dueño de las tablas). Requiere 002.
--   2. Pega el archivo y escribe la contraseña en v_password (abajo).
--      NO guardes este archivo con la contraseña ni lo subas al repositorio:
--      cámbiala solo en la hoja de Database Actions.
--   3. "Ejecutar script" (F5). Se puede repetir: si el usuario ya existe,
--      solo vuelve a dar los permisos (no cambia la contraseña).
--
-- Requisitos de contraseña en Autonomous Database:
--   - De 12 a 30 caracteres, con al menos una mayúscula, una minúscula y un
--     número.
--   - Sin comillas dobles (") y sin contener el nombre del usuario.
--   - No puede repetir una de las últimas 4 contraseñas.
--   Para generar una en el servidor (24 caracteres aleatorios + "Aa1"):
--     python3 -c "import secrets; print(secrets.token_urlsafe(18) + 'Aa1')"
--
-- Después, en backend/.env del servidor:
--   DB_USER=AMATISTA_APP
--   DB_PASSWORD=<la contraseña>
--   DB_ESQUEMA=ADMIN        <- dueño de las tablas (el que ejecutó este script)
-- y comprueba:
--   python diagnostico_oracle.py   -> "... como AMATISTA_APP (esquema ADMIN)"
--   sudo systemctl restart amatista-backend
--
-- Cambiar la contraseña más adelante (y luego actualizar backend/.env):
--   ALTER USER amatista_app IDENTIFIED BY "<la contraseña nueva>";
-- Volver atrás: deja DB_USER=ADMIN en backend/.env y ejecuta
--   DROP USER amatista_app;   (no tiene tablas propias: no borra datos)
-- Una tabla nueva en un script 005+ se agrega a la lista de abajo y se
-- vuelve a ejecutar este archivo.
-- =============================================================================

SET SERVEROUTPUT ON
SET DEFINE OFF

DECLARE
  -- Escribe aquí la contraseña, entre las comillas simples.
  v_password  VARCHAR2(60) := 'ESCRIBE_AQUI_LA_CONTRASENA';
  v_cuenta    PLS_INTEGER;
  v_tablas    SYS.ODCIVARCHAR2LIST := SYS.ODCIVARCHAR2LIST(
                'USUARIOS', 'SESIONES', 'PROGRESO_LECCIONES', 'LOGROS',
                'EVENTOS_APRENDIZAJE', 'CURSOS', 'MODULOS', 'LECCIONES');
BEGIN
  -- Las 8 tablas deben existir en este esquema (002 aplicado).
  FOR i IN 1 .. v_tablas.COUNT LOOP
    SELECT COUNT(*) INTO v_cuenta FROM user_tables WHERE table_name = v_tablas(i);
    IF v_cuenta = 0 THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta la tabla ' || v_tablas(i)
        || ' en este esquema: ejecuta 004 con el dueño de las tablas y después de 002.');
    END IF;
  END LOOP;

  SELECT COUNT(*) INTO v_cuenta FROM all_users WHERE username = 'AMATISTA_APP';
  IF v_cuenta = 0 THEN
    IF v_password = 'ESCRIBE_AQUI_LA_CONTRASENA' THEN
      RAISE_APPLICATION_ERROR(-20002, 'Escribe la contraseña de AMATISTA_APP en v_password y vuelve a ejecutar.');
    END IF;
    IF INSTR(v_password, '"') > 0 THEN
      RAISE_APPLICATION_ERROR(-20003, 'La contraseña no puede tener comillas dobles.');
    END IF;
    EXECUTE IMMEDIATE 'CREATE USER amatista_app IDENTIFIED BY "' || v_password || '"';
    DBMS_OUTPUT.PUT_LINE('Usuario AMATISTA_APP creado.');
  ELSE
    DBMS_OUTPUT.PUT_LINE('AMATISTA_APP ya existía: solo se vuelven a dar los permisos.');
  END IF;

  EXECUTE IMMEDIATE 'GRANT CREATE SESSION TO amatista_app';
  FOR i IN 1 .. v_tablas.COUNT LOOP
    EXECUTE IMMEDIATE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ' || v_tablas(i) || ' TO amatista_app';
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('Permisos listos. Configura DB_USER=AMATISTA_APP y DB_ESQUEMA='
    || SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') || ' en backend/.env.');
END;
/

-- Verificar: CREATE SESSION y 4 permisos por cada una de las 8 tablas (32 filas).
SELECT privilege
  FROM dba_sys_privs
 WHERE grantee = 'AMATISTA_APP';

SELECT table_name, privilege
  FROM user_tab_privs
 WHERE grantee = 'AMATISTA_APP'
 ORDER BY table_name, privilege;

-- Vencimiento de la contraseña: anótalo para cambiarla antes de esa fecha.
SELECT username, account_status, expiry_date
  FROM dba_users
 WHERE username = 'AMATISTA_APP';
