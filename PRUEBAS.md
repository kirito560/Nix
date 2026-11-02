# Resultado de pytest

Ejecutado el 2026-11-02 18:15 UTC en el servidor (Ubuntu, Python 3.13), con `uv run --python .venv pytest -v`.
Las pruebas de audio usan un sounddevice falso y señales sintéticas: no tocan una tarjeta de sonido real.
Las de Groq usan un servidor HTTP falso: no gastan la clave.

**============================= 170 passed in 21.04s =============================**

```
tests/test_aec.py::test_cancela_el_eco_cuando_solo_habla_ella PASSED
tests/test_aec.py::test_respeta_la_voz_del_usuario_mientras_ella_habla PASSED
tests/test_aec.py::test_sin_referencia_no_toca_el_microfono PASSED
tests/test_aec.py::test_remuestreo_y_retardo PASSED
tests/test_aec.py::test_audio_duplex_con_tarjeta_falsa PASSED
tests/test_audio_cliente.py::test_a_mono_promedia_los_canales PASSED
tests/test_audio_cliente.py::test_a_canales_duplica_el_mono PASSED
tests/test_audio_cliente.py::test_remuestreador_por_bloques_sigue_un_tono_continuo PASSED
tests/test_audio_cliente.py::test_tramador_entrega_tramas_exactas PASSED
tests/test_audio_cliente.py::test_cola_bytes_lee_y_completa_con_silencio PASSED
tests/test_audio_cliente.py::test_info_usa_los_canales_y_la_frecuencia_reales PASSED
tests/test_audio_cliente.py::test_microfono_abre_con_sus_canales_y_entrega_mono_16k PASSED
tests/test_audio_cliente.py::test_altavoz_lleva_la_voz_a_sus_canales_y_frecuencia PASSED
tests/test_audio_cliente.py::test_altavoz_que_no_abre_no_tumba_el_programa PASSED
tests/test_audio_cliente.py::test_sin_duplex_abre_microfono_y_altavoz_por_separado PASSED
tests/test_audio_cliente.py::test_apis_distintas_avisan_antes_de_intentar_el_duplex PASSED
tests/test_audio_cliente.py::test_microfono_que_no_abre_reintenta_sin_tumbar_el_programa PASSED
tests/test_audio_cliente.py::test_con_duplex_usa_los_canales_de_cada_lado PASSED
tests/test_audio_cliente.py::test_listar_muestra_dispositivos_y_los_elegidos PASSED
tests/test_audio_cliente.py::test_listar_avisa_si_el_dispositivo_no_existe PASSED
tests/test_audio_cliente.py::test_probar_audio_bien PASSED
tests/test_audio_cliente.py::test_probar_audio_detecta_micro_mudo_y_altavoz_roto PASSED
tests/test_audio_cliente.py::test_probar_audio_todos PASSED
tests/test_audio_cliente.py::test_probar_audio_desde_main_sale_con_codigo PASSED
tests/test_auth.py::test_firma_y_verificacion PASSED
tests/test_auth.py::test_la_firma_de_un_rol_no_sirve_para_el_otro PASSED
tests/test_auth.py::test_el_token_no_aparece_en_la_firma PASSED
tests/test_auth.py::test_token_corto_rechazado PASSED
tests/test_edge_pool.py::test_reusa_conexiones_y_repone_las_reservas PASSED
tests/test_edge_pool.py::test_si_la_conexion_del_pool_esta_muerta_abre_otra PASSED
tests/test_edge_pool.py::test_sin_conexion_usa_el_respaldo PASSED
tests/test_edge_pool.py::test_una_frase_cortada_cierra_su_conexion PASSED
tests/test_edge_pool.py::test_si_falla_a_mitad_no_repite_la_frase PASSED
tests/test_edge_pool.py::test_sin_actividad_cierra_las_reservas PASSED
tests/test_filtros.py::test_alucinaciones[Gracias.] PASSED
tests/test_filtros.py::test_alucinaciones[\xa1Muchas gracias!] PASSED
tests/test_filtros.py::test_alucinaciones[[M\xfasica]] PASSED
tests/test_filtros.py::test_alucinaciones[Subt\xedtulos realizados por la comunidad de Amara.org] PASSED
tests/test_filtros.py::test_alucinaciones[Mmm] PASSED
tests/test_filtros.py::test_alucinaciones[] PASSED
tests/test_filtros.py::test_frases_reales[Gracias por ayudarme con el c\xf3digo] PASSED
tests/test_filtros.py::test_frases_reales[Hola Nix, \xbfjugamos?] PASSED
tests/test_filtros.py::test_validar_por_no_speech_y_por_poca_voz PASSED
tests/test_frases.py::test_corta_en_fin_de_oracion PASSED
tests/test_frases.py::test_la_primera_frase_sale_en_la_primera_coma_larga PASSED
tests/test_frases.py::test_solo_la_primera_frase_corta_en_comas PASSED
tests/test_frases.py::test_frases_muy_largas_se_cortan_en_un_espacio PASSED
tests/test_frases.py::test_limpieza_para_voz PASSED
tests/test_gateway.py::test_conversacion_de_punta_a_punta PASSED
tests/test_gateway.py::test_barge_in_la_interrumpe PASSED
tests/test_gateway.py::test_ruido_corto_mientras_habla_no_la_interrumpe PASSED
tests/test_gateway.py::test_token_incorrecto_cierra_la_conexion PASSED
tests/test_gateway.py::test_sin_autenticar_no_se_acepta_audio PASSED
tests/test_gateway.py::test_ip_fuera_de_la_red_permitida PASSED
tests/test_gateway.py::test_el_cliente_rechaza_un_servidor_falso PASSED
tests/test_gateway.py::test_redes_permitidas_por_defecto PASSED
tests/test_gateway.py::test_la_escucha_adelanta_la_transcripcion_en_las_pausas PASSED
tests/test_gateway.py::test_el_cliente_se_reconecta_si_el_servidor_reinicia PASSED
tests/test_juegos.py::test_marcador_por_persona PASSED
tests/test_juegos.py::test_racha_en_el_texto PASSED
tests/test_juegos.py::test_marcador_persiste_entre_conversaciones PASSED
tests/test_juegos.py::test_resultado_invalido PASSED
tests/test_juegos.py::test_banco_tiene_ids_unicos_y_todos_los_tipos PASSED
tests/test_juegos.py::test_reto_estable_en_el_dia_y_variado PASSED
tests/test_juegos.py::test_es_correcta_ignora_acentos_y_mayusculas PASSED
tests/test_juegos.py::test_acertar_el_reto_suma_al_marcador PASSED
tests/test_juegos.py::test_agotar_intentos_gana_el_personaje PASSED
tests/test_juegos.py::test_polemica_no_se_califica PASSED
tests/test_latencia.py::test_percentil_igual_que_numpy PASSED
tests/test_latencia.py::test_span_y_registro PASSED
tests/test_llm.py::test_groq_en_streaming_solo_lee_el_contenido PASSED
tests/test_llm.py::test_reasoning_effort_solo_en_gpt_oss PASSED
tests/test_llm.py::test_error_de_groq PASSED
tests/test_llm.py::test_stub_y_eleccion PASSED
tests/test_llm.py::test_si_groq_retira_el_modelo_pasa_al_respaldo PASSED
tests/test_orquestador.py::test_turno_completo PASSED
tests/test_orquestador.py::test_historial_entre_turnos PASSED
tests/test_orquestador.py::test_interrumpir_corta_y_manda_flush PASSED
tests/test_orquestador.py::test_si_falla_el_llm_no_se_cae PASSED
tests/test_personaje.py::test_nix_por_defecto PASSED
tests/test_personaje.py::test_personaje_elegido_por_entorno PASSED
tests/test_personaje.py::test_cada_personaje_tiene_su_carpeta_de_datos PASSED
tests/test_personaje.py::test_personaje_inexistente PASSED
tests/test_personaje.py::test_cabecera_obligatoria PASSED
tests/test_protocol.py::test_audio_in_ida_y_vuelta PASSED
tests/test_protocol.py::test_audio_in_mal_formado[] PASSED
tests/test_protocol.py::test_audio_in_mal_formado[1234567] PASSED
tests/test_protocol.py::test_audio_in_mal_formado[12345678\x01] PASSED
tests/test_protocol.py::test_audio_in_rechaza_pcm_impar PASSED
tests/test_protocol.py::test_audio_out_ida_y_vuelta PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[auth.challenge] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[auth.response] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[auth.ok] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[vision.delta0] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[vision.delta1] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[vision.frame] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[user.state] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[playback.ack] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[stt.text] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[avatar.cmd] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[playback.flush] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[state.sync] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[llm.text] PASSED
tests/test_protocol.py::test_mensajes_ida_y_vuelta[error] PASSED
tests/test_protocol.py::test_todos_los_tipos_estan_cubiertos PASSED
tests/test_protocol.py::test_mensajes_invalidos[{"type": "desconocido"}] PASSED
tests/test_protocol.py::test_mensajes_invalidos[{"nonce": "sin tipo"}] PASSED
tests/test_protocol.py::test_mensajes_invalidos[no es json] PASSED
tests/test_protocol.py::test_mensajes_invalidos[{"type": "stt.text", "turn_id": "t", "texto": "x", "campo_extra": 1}] PASSED
tests/test_protocol.py::test_mensajes_invalidos[{"type": "user.state", "ts": 1, "valence": 2.0, "attention": 0.5}] PASSED
tests/test_senal.py::test_pasa_altas_quita_graves_y_continua_y_deja_la_voz PASSED
tests/test_senal.py::test_pasa_altas_por_tramas_igual_que_de_una_vez PASSED
tests/test_senal.py::test_normalizador_lleva_la_voz_al_objetivo PASSED
tests/test_senal.py::test_normalizador_no_sube_mas_que_el_tope PASSED
tests/test_senal.py::test_normalizador_no_amplifica_el_ruido PASSED
tests/test_senal.py::test_normalizador_nunca_satura_y_cambia_sin_saltos PASSED
tests/test_senal.py::test_acondicionador_quita_graves_y_normaliza PASSED
tests/test_senal.py::test_guarda_eco_limita_la_salida_a_3_db PASSED
tests/test_senal.py::test_guarda_eco_reinicia_el_filtro_desajustado PASSED
tests/test_senal.py::test_guarda_eco_respeta_una_buena_cancelacion PASSED
tests/test_senal.py::test_guarda_eco_con_solo_ruido_deja_el_microfono PASSED
tests/test_senal.py::test_guarda_eco_cambia_sin_cortes PASSED
tests/test_senal.py::test_guarda_eco_informa_cada_5_segundos PASSED
tests/test_senal.py::test_remuestreo_de_48_y_22_khz_a_16_khz PASSED
tests/test_senal.py::test_remuestreo_quita_lo_que_no_cabe_en_16_khz PASSED
tests/test_senal.py::test_al_servidor_van_tramas_pcm16_mono_16k_acondicionadas PASSED
tests/test_stt.py::test_stub_devuelve_texto_simulado PASSED
tests/test_stt.py::test_crear_stt_sin_claves_usa_stub PASSED
tests/test_stt.py::test_deepgram_con_servidor_falso PASSED
tests/test_stt.py::test_groq_acepta_frase_valida PASSED
tests/test_stt.py::test_groq_descarta_alucinacion PASSED
tests/test_stt.py::test_groq_no_llama_a_la_api_sin_voz PASSED
tests/test_stt.py::test_groq_usa_la_transcripcion_adelantada PASSED
tests/test_stt.py::test_groq_descarta_el_adelanto_si_siguio_hablando PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[] PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[   ] PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[.] PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[a] PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[\xbf?] PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[\xa1A!] PASSED
tests/test_transcripcion.py::test_textos_vacios_o_de_un_caracter[None] PASSED
tests/test_transcripcion.py::test_textos_validos[s\xed] PASSED
tests/test_transcripcion.py::test_textos_validos[Hola] PASSED
tests/test_transcripcion.py::test_textos_validos[no, Nix] PASSED
tests/test_transcripcion.py::test_repite_el_prompt PASSED
tests/test_transcripcion.py::test_confianza_baja PASSED
tests/test_transcripcion.py::test_groq_manda_espanol_y_el_prompt_de_contexto PASSED
tests/test_transcripcion.py::test_groq_sin_prompt_no_lo_manda PASSED
tests/test_transcripcion.py::test_groq_con_confianza_baja_pide_repetir PASSED
tests/test_transcripcion.py::test_groq_umbral_de_confianza_configurable PASSED
tests/test_transcripcion.py::test_groq_descarta_el_prompt_repetido PASSED
tests/test_transcripcion.py::test_groq_descarta_un_solo_caracter PASSED
tests/test_transcripcion.py::test_texto_vacio_no_llega_al_llm PASSED
tests/test_transcripcion.py::test_confianza_baja_nix_pide_que_lo_repitas PASSED
tests/test_transcripcion.py::test_si_falla_el_stt_avisa_y_sigue_escuchando PASSED
tests/test_transcripcion.py::test_si_falla_el_llm_dice_la_frase_de_error_y_sigue PASSED
tests/test_transcripcion.py::test_decir_no_se_guarda_en_el_historial PASSED
tests/test_transcripcion.py::test_medidor_cada_10_s_con_rms_duracion_y_voces PASSED
tests/test_transcripcion.py::test_espera_de_reconexion_crece_hasta_10_s PASSED
tests/test_transcripcion.py::test_conversar_siempre_espera_entre_reintentos PASSED
tests/test_transcripcion.py::test_no_reintenta_si_el_token_es_incorrecto PASSED
tests/test_tts.py::test_stub_da_pcm_con_la_duracion_de_la_frase PASSED
tests/test_tts.py::test_edge_decodifica_el_mp3_en_streaming PASSED
tests/test_tts.py::test_edge_propaga_errores_de_la_fuente PASSED
tests/test_tts.py::test_eleccion_de_tts PASSED
tests/test_vad.py::test_reagrupador_hace_ventanas_de_512 PASSED
tests/test_vad.py::test_silencio_no_genera_eventos PASSED
tests/test_vad.py::test_frase_completa PASSED
tests/test_vad.py::test_ruido_corto_se_descarta PASSED
tests/test_vad.py::test_dos_frases_separadas PASSED
============================= 170 passed in 21.04s =============================
```
