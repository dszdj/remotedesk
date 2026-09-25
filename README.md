# Remote Desktop Control

Projeto MVP de controle remoto em Python com servidor web e interface no navegador.

## Funcionalidades atuais

- servidor central em FastAPI
- painel web para selecionar e controlar máquinas remotas
- tela do cliente em tempo real via WebSocket
- clique, movimento e clique direito no desktop remoto
- envio de teclado e atalhos remotos
- múltiplos clientes conectados

## Estrutura

- `server.py`: servidor web + roteamento de comandos
- `client.py`: cliente que captura e controla a máquina remota
- `templates/control.html`: painel do operador
- `requirements.txt`: dependências do projeto
- `run_server.bat`: inicia o servidor no Windows
- `run_client.bat`: inicia o cliente no Windows

## Como rodar

Servidor:

```bat
run_server.bat
```

Acesse:

```text
http://localhost:8000
```

Cliente remoto:

```bat
run_client.bat
```

Você pode ajustar o ID da máquina no script ou rodar diretamente:

```bat
python client.py --server ws://localhost:8000/ws --client-id desktop-01
```

## Fluxo de uso

1. O cliente conecta ao servidor.
2. O cliente transmite a tela em JPG em base64.
3. O operador seleciona a máquina no painel web.
4. A tela remota aparece no navegador.
5. O operador clica, move o mouse ou digita utilizando o teclado do navegador.
6. O cliente executa esses comandos na máquina alvo via `pyautogui`.

## Observações

Esse é um MVP funcional para aprendizado e suporte remoto local. Para produção, vale adicionar:
- autenticação de usuários
- segurança com TLS
- registros e auditoria
- múltiplas sessões simultâneas
- transferência de arquivos
- conexão estável com reconexão automática
