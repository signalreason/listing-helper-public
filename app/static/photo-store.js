const DATABASE_NAME = "listing-helper-draft-photos";
const DATABASE_VERSION = 2;
const PHOTO_STORE = "photos";
const DRAFT_STORE = "drafts";
const RESERVATION_STORE = "reservations";
const RESERVATION_MAX_AGE_MS = 15 * 60 * 1000;

export const PHOTO_STORAGE_LIMIT = 1024 * 1024 * 1024;
export const PHOTO_STORAGE_WARNING = PHOTO_STORAGE_LIMIT * 0.75;
export const PHOTO_STORAGE_ALMOST_FULL = 900 * 1024 * 1024;

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener("error", () => reject(request.error), { once: true });
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.addEventListener("complete", resolve, { once: true });
    transaction.addEventListener("abort", () => reject(transaction.error), { once: true });
    transaction.addEventListener("error", () => reject(transaction.error), { once: true });
  });
}

export function openPhotoStore() {
  if (!globalThis.indexedDB) return Promise.reject(new Error("Browser photo storage is unavailable."));
  const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
  request.addEventListener("upgradeneeded", () => {
    const database = request.result;
    if (!database.objectStoreNames.contains(PHOTO_STORE)) {
      const photos = database.createObjectStore(PHOTO_STORE, {
        keyPath: ["accountId", "draftId", "position"],
      });
      photos.createIndex("accountDraft", ["accountId", "draftId"]);
    }
    if (!database.objectStoreNames.contains(DRAFT_STORE)) {
      const drafts = database.createObjectStore(DRAFT_STORE, {
        keyPath: ["accountId", "draftId"],
      });
      drafts.createIndex("account", "accountId");
    }
    if (!database.objectStoreNames.contains(RESERVATION_STORE)) {
      database.createObjectStore(RESERVATION_STORE, { keyPath: "id" });
    }
  });
  return requestResult(request);
}

async function deleteDraftInTransaction(transaction, accountId, draftId) {
  const photos = transaction.objectStore(PHOTO_STORE);
  const keys = await requestResult(
    photos.index("accountDraft").getAllKeys(IDBKeyRange.only([accountId, draftId])),
  );
  keys.forEach((key) => photos.delete(key));
  transaction.objectStore(DRAFT_STORE).delete([accountId, draftId]);
}

function photoStorageLimitError() {
  const error = new Error("Draft photo storage would exceed the 1 GB limit.");
  error.name = "PhotoStorageLimitError";
  return error;
}

function staleDraftPhotoWriteError() {
  const error = new Error("A newer draft photo set is already stored in this browser.");
  error.name = "StaleDraftPhotoWriteError";
  return error;
}

function deletedDraftPhotoWriteError() {
  const error = new Error("This draft was deleted from this browser.");
  error.name = "DeletedDraftPhotoWriteError";
  return error;
}

function activeReservationBytes(reservations, excludedId = null) {
  const oldestAllowed = Date.now() - RESERVATION_MAX_AGE_MS;
  return reservations.reduce(
    (total, reservation) =>
      reservation.id !== excludedId && reservation.createdAt >= oldestAllowed
        ? total + reservation.bytes
        : total,
    0,
  );
}

export async function reservePhotoCapacity(database, reservationId, bytes) {
  const transaction = database.transaction([DRAFT_STORE, RESERVATION_STORE], "readwrite");
  const done = transactionDone(transaction);
  const drafts = await requestResult(transaction.objectStore(DRAFT_STORE).getAll());
  const reservationStore = transaction.objectStore(RESERVATION_STORE);
  const reservations = await requestResult(reservationStore.getAll());
  const totalBytes = drafts.reduce((total, draft) => total + draft.bytes, 0);
  if (totalBytes + activeReservationBytes(reservations) + bytes > PHOTO_STORAGE_LIMIT) {
    transaction.abort();
    try {
      await done;
    } catch {
      // The product-limit error below is more useful than the transaction abort.
    }
    throw photoStorageLimitError();
  }
  reservations
    .filter((reservation) => reservation.createdAt < Date.now() - RESERVATION_MAX_AGE_MS)
    .forEach((reservation) => reservationStore.delete(reservation.id));
  reservationStore.put({ id: reservationId, bytes, createdAt: Date.now() });
  await done;
}

export async function releasePhotoCapacity(database, reservationId) {
  const transaction = database.transaction(RESERVATION_STORE, "readwrite");
  const done = transactionDone(transaction);
  transaction.objectStore(RESERVATION_STORE).delete(reservationId);
  await done;
}

export async function saveDraftPhotos(
  database,
  accountId,
  draftId,
  files,
  draftRevision,
  reservationId = null,
) {
  const transaction = database.transaction(
    [PHOTO_STORE, DRAFT_STORE, RESERVATION_STORE],
    "readwrite",
  );
  const done = transactionDone(transaction);
  const drafts = transaction.objectStore(DRAFT_STORE);
  const records = await requestResult(drafts.getAll());
  const reservations = await requestResult(
    transaction.objectStore(RESERVATION_STORE).getAll(),
  );
  const existing = records.find(
    (record) => record.accountId === accountId && record.draftId === draftId,
  );
  if (existing?.deleted) {
    transaction.abort();
    try {
      await done;
    } catch {
      // The deleted-draft error below explains the safe rejection.
    }
    throw deletedDraftPhotoWriteError();
  }
  const existingBytes = existing?.bytes || 0;
  if (
    existing?.draftRevision &&
    (!draftRevision || draftRevision < existing.draftRevision)
  ) {
    transaction.abort();
    try {
      await done;
    } catch {
      // The stale-write error below explains the safe rejection.
    }
    throw staleDraftPhotoWriteError();
  }
  const newBytes = files.reduce((total, file) => total + file.size, 0);
  const totalBytes = records.reduce((total, record) => total + record.bytes, 0);
  const reservedBytes = activeReservationBytes(reservations, reservationId);
  if (totalBytes - existingBytes + newBytes + reservedBytes > PHOTO_STORAGE_LIMIT) {
    transaction.abort();
    try {
      await done;
    } catch {
      // The product-limit error below is more useful than the transaction abort.
    }
    throw photoStorageLimitError();
  }
  await deleteDraftInTransaction(transaction, accountId, draftId);
  const photos = transaction.objectStore(PHOTO_STORE);
  files.forEach((file, index) => {
    photos.put({
      accountId,
      draftId,
      position: index + 1,
      name: file.name,
      type: file.type,
      size: file.size,
      lastModified: file.lastModified,
      blob: file,
    });
  });
  drafts.put({
    accountId,
    draftId,
    bytes: newBytes,
    photoCount: files.length,
    draftRevision,
  });
  if (reservationId) transaction.objectStore(RESERVATION_STORE).delete(reservationId);
  await done;
}

export async function advanceDraftPhotoRevision(
  database,
  accountId,
  draftId,
  previousRevision,
  draftRevision,
) {
  const transaction = database.transaction(DRAFT_STORE, "readwrite");
  const done = transactionDone(transaction);
  const drafts = transaction.objectStore(DRAFT_STORE);
  const existing = await requestResult(drafts.get([accountId, draftId]));
  if (
    !existing ||
    existing.deleted ||
    existing.draftRevision !== previousRevision ||
    draftRevision < previousRevision
  ) {
    transaction.abort();
    try {
      await done;
    } catch {
      // The stale-write error below explains the safe rejection.
    }
    throw staleDraftPhotoWriteError();
  }
  drafts.put({ ...existing, draftRevision });
  await done;
}

export async function loadDraftPhotos(database, accountId, draftId, draftRevision) {
  const transaction = database.transaction([PHOTO_STORE, DRAFT_STORE], "readonly");
  const done = transactionDone(transaction);
  const draftRecord = await requestResult(
    transaction.objectStore(DRAFT_STORE).get([accountId, draftId]),
  );
  const records = await requestResult(
    transaction.objectStore(PHOTO_STORE).index("accountDraft").getAll(
      IDBKeyRange.only([accountId, draftId]),
    ),
  );
  await done;
  if (!draftRecord || draftRecord.deleted || draftRecord.draftRevision !== draftRevision) return [];
  return records
    .sort((left, right) => left.position - right.position)
    .map(
      (record) =>
        new File([record.blob], record.name, {
          type: record.type,
          lastModified: record.lastModified,
        }),
    );
}

export async function markDraftPhotoDeletion(database, accountId, draftId, deletionToken) {
  const transaction = database.transaction(DRAFT_STORE, "readwrite");
  const done = transactionDone(transaction);
  const drafts = transaction.objectStore(DRAFT_STORE);
  const existing = await requestResult(drafts.get([accountId, draftId]));
  drafts.put({
    accountId,
    draftId,
    bytes: existing?.bytes || 0,
    photoCount: existing?.photoCount || 0,
    draftRevision: existing?.draftRevision || null,
    deleted: true,
    deletionToken,
    deletionPending: true,
  });
  await done;
}

export async function clearDraftPhotoDeletion(database, accountId, draftId, deletionToken) {
  const transaction = database.transaction(DRAFT_STORE, "readwrite");
  const done = transactionDone(transaction);
  const drafts = transaction.objectStore(DRAFT_STORE);
  const existing = await requestResult(drafts.get([accountId, draftId]));
  let cleared = false;
  if (existing?.deleted && existing.deletionToken === deletionToken) {
    const restored = { ...existing };
    delete restored.deleted;
    delete restored.deletionToken;
    delete restored.deletionPending;
    drafts.put(restored);
    cleared = true;
  }
  await done;
  return cleared;
}

export async function deleteDraftPhotos(database, accountId, draftId, deletionToken = null) {
  const transaction = database.transaction([PHOTO_STORE, DRAFT_STORE], "readwrite");
  const done = transactionDone(transaction);
  const existing = await requestResult(
    transaction.objectStore(DRAFT_STORE).get([accountId, draftId]),
  );
  await deleteDraftInTransaction(transaction, accountId, draftId);
  transaction.objectStore(DRAFT_STORE).put({
    accountId,
    draftId,
    bytes: 0,
    photoCount: 0,
    draftRevision: null,
    deleted: true,
    deletionToken: deletionToken || existing?.deletionToken || null,
    deletionPending: false,
  });
  await done;
}

export async function pendingDraftPhotoDeletions(database, accountId) {
  const transaction = database.transaction(DRAFT_STORE, "readonly");
  const done = transactionDone(transaction);
  const records = await requestResult(transaction.objectStore(DRAFT_STORE).getAll());
  await done;
  return records
    .filter(
      (record) =>
        record.accountId === accountId &&
        record.deleted &&
        record.deletionPending &&
        record.deletionToken,
    )
    .map((record) => ({
      accountId: record.accountId,
      draftId: record.draftId,
      token: record.deletionToken,
    }));
}

export async function photoStorageStats(database, accountId, draftId = null) {
  const transaction = database.transaction(DRAFT_STORE, "readonly");
  const done = transactionDone(transaction);
  const records = await requestResult(transaction.objectStore(DRAFT_STORE).getAll());
  await done;
  return {
    totalBytes: records.reduce((total, record) => total + record.bytes, 0),
    currentBytes: draftId
      ? records.find((record) => record.accountId === accountId && record.draftId === draftId)
          ?.bytes || 0
      : 0,
  };
}

export function selectedPhotoBytes(files) {
  return files.reduce((total, file) => total + file.size, 0);
}

export async function browserHasRoomFor(bytes) {
  if (!navigator.storage?.estimate) return true;
  const estimate = await navigator.storage.estimate();
  if (!Number.isFinite(estimate.quota) || !Number.isFinite(estimate.usage)) return true;
  return estimate.quota - estimate.usage >= bytes;
}
