const dbName = process.env.MONGO_DB_NAME || "ssh_bot";
const user = process.env.MONGO_USER || "ssh_bot_user";
const password = process.env.MONGO_PASSWORD || "ssh_bot_password";

db = db.getSiblingDB(dbName);

try {
  db.createUser({
    user: user,
    pwd: password,
    roles: [{ role: "readWrite", db: dbName }],
  });
} catch (e) {
  print(`createUser skipped: ${e}`);
}
