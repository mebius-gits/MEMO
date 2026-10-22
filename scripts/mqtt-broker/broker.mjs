// Ephemeral local test broker; production Compose uses Mosquitto.
import { Aedes } from 'aedes';
import { createServer } from 'node:net';

const broker = await Aedes.createBroker();
const server = createServer(broker.handle);
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
process.on('SIGTERM', () => broker.close(() => server.close(() => process.exit(0))));
