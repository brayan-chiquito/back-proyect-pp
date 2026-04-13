"""Prompts y respuestas rápidas — comida (constantes, no DB)."""

SYSTEM_PROMPT = (
    "Eres el asistente virtual de un negocio de comida por WhatsApp. "
    "Responde en español. Confirma alérgenos y método de pago."
)

QUICK_REPLIES: tuple[str, ...] = (
    "¡Hola! Cocina a pedido; dime si buscas menú o algo especial.",
    "Servimos de lunes a domingo 11:30–21:00; feriados igual salvo aviso.",
    "El punto de recogida o mapa te lo enviamos al coordinar el pedido.",
    "Recibimos efectivo, Nequi, Daviplata y transferencia.",
    "Domicilio con costo por distancia; lo confirmamos al tomar el pedido.",
    "¡Gracias! Que te caiga bien la comida.",
    "¡Nos vemos! Escribe cuando quieras volver a pedir.",
    "Menú del día",
    "Domicilios",
    "Métodos de pago",
)
