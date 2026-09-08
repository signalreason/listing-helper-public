import {
  PHOTO_STORAGE_ALMOST_FULL,
  PHOTO_STORAGE_LIMIT,
  PHOTO_STORAGE_WARNING,
  advanceDraftPhotoRevision,
  browserHasRoomFor,
  clearDraftPhotoDeletion,
  deleteDraftPhotos,
  loadDraftPhotos,
  markDraftPhotoDeletion,
  openPhotoStore,
  pendingDraftPhotoDeletions,
  photoStorageStats,
  releasePhotoCapacity,
  reservePhotoCapacity,
  saveDraftPhotos,
  selectedPhotoBytes,
} from "./photo-store.js?v=20260906-1";

const MAX_PHOTOS = 24;
const MAX_FILE_BYTES = 12 * 1024 * 1024;
const LEGACY_LAST_EBAY_TRANSFER_KEY = "lastEbayTransferId";

const form = document.querySelector("#listing-form");
const input = document.querySelector("#photo-input");
const dropZone = document.querySelector("#drop-zone");
const photoList = document.querySelector("#photo-list");
const photoCount = document.querySelector("#photo-count");
const notes = document.querySelector("#notes");
const errorMessage = document.querySelector("#error-message");
const generateButton = document.querySelector("#generate-button");
const generationCategoryGroup = document.querySelector("#generation-category-group");
const generationCategoryRetry = document.querySelector("#generation-category-retry");
const generationCategory = document.querySelector("#generation-category");
const generationCategoryStatus = document.querySelector("#generation-category-status");
const generationReadiness = document.querySelector("#generation-readiness");
const generationCategoryControls = [generationCategoryGroup, generationCategoryRetry, generationCategory];
let generationCategorySearchRevision = 0;
let generationGroupsRequest = null;

const buttonLabel = document.querySelector(".button-label");
const buttonLoading = document.querySelector(".button-loading");
const resultSection = document.querySelector("#result-section");
const draftFields = document.querySelector("#draft-fields");
const warningList = document.querySelector("#warning-list");
const copyButton = document.querySelector("#copy-button");
const accountEmail = document.querySelector("#account-email");
const adminLink = document.querySelector("#admin-link");
const logoutButton = document.querySelector("#logout-button");
const ebayConnectionStatus = document.querySelector("#ebay-connection-status");
const ebayConnectButton = document.querySelector("#ebay-connect-button");
const ebayDisconnectButton = document.querySelector("#ebay-disconnect-button");
const ebayDraftSection = document.querySelector("#ebay-draft-section");
const ebayCategory = document.querySelector("#ebay-category");
const ebayCondition = document.querySelector("#ebay-condition");
const packageWeightPounds = document.querySelector("#package-weight-pounds");
const packageWeightOunces = document.querySelector("#package-weight-ounces");
const packageLength = document.querySelector("#package-length");
const packageWidth = document.querySelector("#package-width");
const packageHeight = document.querySelector("#package-height");
const packageDimensionsStatus = document.querySelector("#package-dimensions-status");
const ebayRequirements = document.querySelector("#ebay-requirements");
const ebayTransferStatus = document.querySelector("#ebay-transfer-status");
const ebayPublishReadiness = document.querySelector("#ebay-publish-readiness");
const sendToEbayButton = document.querySelector("#send-to-ebay-button");
const ebayPolicyStatus = document.querySelector("#ebay-policy-status");
const policyControls = {
  fulfillment_policy: {
    select: document.querySelector("#fulfillment-policy"),
    field: document.querySelector("#fulfillment-policy-field"),
  },
  payment_policy: {
    select: document.querySelector("#payment-policy"),
    field: document.querySelector("#payment-policy-field"),
  },
  return_policy: {
    select: document.querySelector("#return-policy"),
    field: document.querySelector("#return-policy-field"),
  },
};
const packageDimensionControls = [
  { key: "length", label: "length", announcement: "Length", input: packageLength },
  { key: "width", label: "width", announcement: "Width", input: packageWidth },
  { key: "height", label: "height", announcement: "Height", input: packageHeight },
];
const savedDraftsLink = document.querySelector("#saved-drafts-link");
const savedDraftsCount = document.querySelector("#saved-drafts-count");
const savedDraftsSection = document.querySelector("#saved-drafts-section");
const savedDraftsStatus = document.querySelector("#saved-drafts-status");
const savedDraftsList = document.querySelector("#saved-drafts-list");
const browserStorageTotal = document.querySelector("#browser-storage-total");
const browserStorageWarning = document.querySelector("#browser-storage-warning");
const currentPhotoStorage = document.querySelector("#current-photo-storage");
const draftSaveStatus = document.querySelector("#draft-save-status");
const startNewListingButton = document.querySelector("#start-new-listing-button");
const saveDraftButton = document.querySelector("#save-draft-button");
const draftSaveMessage = document.querySelector("#draft-save-message");
const photoReselectMessage = document.querySelector("#photo-reselect-message");
const publishedDraftMessage = document.querySelector("#published-draft-message");

let selectedPhotos = [];
let currentDraft = null;
let reconcileNewGeneration = false;
let ebayConnection = null;
let ebayConnectionState = "loading";
let ebayCategoryRules = null;
let ebayRequirementsValid = false;
let ebayCategoryState = "idle";
let ebayRequirementsState = "idle";
let ebayPoliciesState = "idle";
let ebayPoliciesRevision = 0;
let ebayPublishInFlight = false;
let generationInFlight = false;
let draftDeletionInFlight = false;
let draftOpenInFlight = false;
let currentDraftId = null;
let currentDraftRevision = null;
let currentDraftPublished = false;
let publicationRecoveryPending = false;
let publicationRecoveryPolicies = null;
let publicationRecoveryPreparationId = null;
let currentUserId = null;
let photoDatabase = null;
let photoStorageAvailable = true;
let currentPhotosStored = false;
let currentPhotoCount = 0;
let photoRevision = 0;
let photoRecoveryExpectedCount = null;
let savedEbayCategory = null;
let savedEbayCondition = null;
let saveTimer = null;
let saveInFlight = null;
let saveInFlightRevision = null;
let saveRevision = 0;
let savedRevision = 0;
let categorySuggestionRevision = 0;
let categoryRequirementsRevision = 0;
let draftOpenRevision = 0;
let savedDraftsLoadRevision = 0;
let photoStorageDisplayRevision = 0;
const publicationDisabledControls = new Map();
const generationDisabledControls = new Map();
const draftOpenDisabledControls = new Map();
const packageDimensionRoundingMessages = new Map();
const pendingDeletionDraftIds = new Set();

function selectedGenerationCategory() {
  const option = generationCategory.selectedOptions[0];
  return option?.value ? {
    category_id: option.value, name: option.dataset.name, path: option.textContent,
    group_id: option.dataset.groupId || null,
  } : null;
}

function updateGenerationReadiness() {
  const regenerationMessage = document.querySelector("#category-regeneration-message");
  regenerationMessage.hidden = !currentDraft?.generation_required;
  regenerationMessage.textContent = currentDraft?.generation_required ?
    "Category changed. Select Generate listing again before publication." : "";
  const missing = [];
  if (!selectedPhotos.length) missing.push("Add at least one photo.");
  if (!generationCategory.value) missing.push("Select an eBay category.");
  generationReadiness.textContent = missing.join(" ");
  const locked = generationInFlight || currentDraftPublished ||
    draftOpenInFlight || draftDeletionInFlight || ebayPublishInFlight;
  generateButton.disabled = Boolean(missing.length) || locked;
  generationCategoryControls.forEach((control) => { control.disabled = locked; });
}

async function restoreGenerationCategory(category) {
  const revision = ++generationCategorySearchRevision;
  const draftId = currentDraftId;
  generationCategoryGroup.value = category?.group_id || "";
  generationCategoryStatus.textContent = "";
  generationCategoryRetry.hidden = true;
  generationCategory.replaceChildren(new Option("Select a group first", ""));
  if (category) {
    const option = new Option(category.path, category.category_id);
    option.dataset.name = category.name;
    option.dataset.groupId = category.group_id || "";
    generationCategory.append(option);
    generationCategory.value = category.category_id;
    generationCategoryStatus.textContent = `Selected category: ${category.path}`;
  }
  updateGenerationReadiness();
  if (!category || currentDraftPublished || publicationRecoveryPending) return;
  const isCurrent = () => revision === generationCategorySearchRevision && draftId === currentDraftId;
  if (!(await loadGenerationGroups()) || !isCurrent()) return;
  const groups = category.group_id ? [category.group_id] :
    Array.from(generationCategoryGroup.options, option => option.value).filter(Boolean);
  try {
    for (const group of groups) {
      const categories = await fetchGenerationCategories(group);
      if (!isCurrent()) return;
      if (categories.some(item => item.category_id === category.category_id)) {
        generationCategoryGroup.value = group;
        renderGenerationCategories(group, categories, category);
        if (ebayCategory.value === category.category_id) {
          ebayCategory.selectedOptions[0].dataset.groupId = group;
        }
        return;
      }
    }
    generationCategoryStatus.textContent = "Saved category retained. Select a group to choose another category.";
  } catch (error) {
    if (!isCurrent()) return;
    generationCategoryStatus.textContent = error.message;
    generationCategoryRetry.hidden = false;
  }
}

function markCategoryChange(nextId, previousId) {
  if (currentDraft && !publicationRecoveryPending && nextId !== previousId) {
    currentDraft.generation_required = true;
  }
  updateGenerationReadiness();
}

function applyGenerationCategoryToDraft() {
  if (!currentDraft || currentDraftPublished || publicationRecoveryPending || draftOpenInFlight) return;
  markCategoryChange(selectedGenerationCategory()?.category_id || "", ebayCategory.value);
  reconcileNewGeneration = false;
  invalidateEbayDraftLoaders();
  savedEbayCategory = selectedGenerationCategory();
  savedEbayCondition = null;
  restoreDraftCategory();
  scheduleDraftSave();
  void loadCategoryRequirements();
}

generationCategory.addEventListener("change", () => {
  generationCategorySearchRevision += 1;
  const category = selectedGenerationCategory();
  generationCategoryStatus.textContent = category ? `Selected category: ${category.path}` : "Select a category.";
  updateGenerationReadiness();
  applyGenerationCategoryToDraft();
});
function loadGenerationGroups() {
  if (!generationGroupsRequest) {
    generationGroupsRequest = fetchGenerationGroups().then((loaded) => {
      if (!loaded) generationGroupsRequest = null;
      return loaded;
    });
  }
  return generationGroupsRequest;
}

async function fetchGenerationGroups() {
  try {
    const response = await fetch("/api/ebay/category-groups");
    const payload = await responsePayload(response, "Category groups could not load. Retry.");
    if (!response.ok) throw new Error(payload.detail);
    const selected = generationCategoryGroup.value;
    generationCategoryGroup.replaceChildren(new Option("Select a group", ""));
    payload.groups.forEach((group) => generationCategoryGroup.append(new Option(group.name, group.group_id)));
    generationCategoryGroup.value = selected;
    generationCategoryRetry.hidden = true;
    return true;
  } catch (error) {
    generationCategoryStatus.textContent = error.message;
    generationCategoryRetry.hidden = false;
    return false;
  }
}

async function fetchGenerationCategories(group) {
  const response = await fetch(`/api/ebay/category-groups/${encodeURIComponent(group)}/categories`);
  const payload = await responsePayload(response, "Category loading failed. Retry.");
  if (!response.ok) throw new Error(payload.detail);
  if (payload.group_id !== group) throw new Error("Category group did not match. Retry.");
  return payload.categories;
}

function renderGenerationCategories(group, categories, selected = null) {
  generationCategory.replaceChildren(new Option("Select a category", ""));
  categories.forEach((category) => {
    const option = new Option(category.path, category.category_id);
    option.dataset.name = category.name;
    option.dataset.groupId = group;
    generationCategory.append(option);
  });
  generationCategory.value = selected?.category_id || "";
  generationCategoryStatus.textContent = selected ? `Selected category: ${selected.path}` :
    "Select the category that matches your item.";
  generationCategoryRetry.hidden = true;
  updateGenerationReadiness();
}

async function loadGenerationCategories() {
  const group = generationCategoryGroup.value;
  const revision = ++generationCategorySearchRevision;
  generationCategory.replaceChildren(new Option("Select a category", ""));
  generationCategoryRetry.hidden = true;
  updateGenerationReadiness();
  if (!group) { generationCategoryStatus.textContent = "Select a category group."; return; }
  generationCategoryStatus.textContent = "Loading categories…";
  try {
    const categories = await fetchGenerationCategories(group);
    if (revision !== generationCategorySearchRevision || group !== generationCategoryGroup.value) return;
    renderGenerationCategories(group, categories);
  } catch (error) {
    if (revision !== generationCategorySearchRevision) return;
    generationCategoryStatus.textContent = error.message || "Category loading failed. Retry.";
    generationCategoryRetry.hidden = false;
  }
}

generationCategoryGroup.addEventListener("change", () => {
  if (generationInFlight || draftOpenInFlight || draftDeletionInFlight || ebayPublishInFlight || currentDraftPublished) return;
  loadGenerationCategories();
  applyGenerationCategoryToDraft();
});
generationCategoryRetry.addEventListener("click", async () => {
  if (generationCategoryGroup.options.length === 1 && !(await loadGenerationGroups())) return;
  if (selectedGenerationCategory()) await restoreGenerationCategory(selectedGenerationCategory());
  else await loadGenerationCategories();
});

function restoreDraftCategory() {
  ebayCategory.replaceChildren(new Option("Choose a category", ""));
  if (savedEbayCategory) {
    const option = new Option(savedEbayCategory.path, savedEbayCategory.category_id);
    option.dataset.name = savedEbayCategory.name;
    option.dataset.groupId = savedEbayCategory.group_id || "";
    ebayCategory.append(option);
    ebayCategory.value = savedEbayCategory.category_id;
  }
  ebayCategoryState = "ready";
  ebayCategory.disabled = false;
}

document.querySelector("#draft-category-search").addEventListener("click", loadCategorySuggestions);

function formatStorage(bytes) {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${Math.ceil(bytes / (1024 * 1024))} MB`;
  if (bytes >= 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${bytes} bytes`;
}

function renderStorageWarning(totalBytes) {
  if (!photoStorageAvailable) {
    browserStorageWarning.textContent =
      "This browser cannot save draft photos. Server drafts still work, but you must select photos again on this device.";
    browserStorageWarning.hidden = false;
    return;
  }
  if (totalBytes >= PHOTO_STORAGE_LIMIT) {
    browserStorageWarning.textContent =
      "Draft photo storage has reached its 1 GB limit. Delete drafts before you create another draft.";
  } else if (totalBytes >= PHOTO_STORAGE_ALMOST_FULL) {
    browserStorageWarning.textContent =
      "Draft photo storage is almost full. New photos must fit within the 1 GB limit. Delete drafts to reclaim space.";
  } else if (totalBytes > PHOTO_STORAGE_WARNING) {
    browserStorageWarning.textContent =
      "Draft photos are using more than 75% of the 1 GB limit. Delete drafts to reclaim space.";
  } else {
    browserStorageWarning.textContent = "";
    browserStorageWarning.hidden = true;
    return;
  }
  browserStorageWarning.hidden = false;
}

async function refreshPhotoStorageDisplay() {
  const displayRevision = ++photoStorageDisplayRevision;
  const displayedDraftId = currentDraftId;
  const displayedPhotoRevision = photoRevision;
  const selectedBytes = selectedPhotoBytes(selectedPhotos);
  currentPhotoStorage.textContent = `Current photos: ${formatStorage(selectedBytes)}`;
  if (!photoStorageAvailable || !photoDatabase || currentUserId === null) {
    browserStorageTotal.textContent = "Draft photo storage in this browser: unavailable";
    renderStorageWarning(0);
    return { totalBytes: 0, currentBytes: 0 };
  }
  try {
    const stats = await photoStorageStats(photoDatabase, currentUserId, displayedDraftId);
    if (
      displayRevision !== photoStorageDisplayRevision ||
      displayedDraftId !== currentDraftId ||
      displayedPhotoRevision !== photoRevision
    ) {
      return stats;
    }
    const currentBytes = selectedBytes || stats.currentBytes;
    currentPhotoStorage.textContent = `Current photos: ${formatStorage(currentBytes)}`;
    browserStorageTotal.textContent =
      `Draft photo storage in this browser: ${formatStorage(stats.totalBytes)} of 1 GB`;
    renderStorageWarning(stats.totalBytes);
    return stats;
  } catch {
    photoStorageAvailable = false;
    browserStorageTotal.textContent = "Draft photo storage in this browser: unavailable";
    renderStorageWarning(0);
    return { totalBytes: 0, currentBytes: 0 };
  }
}

async function persistCurrentDraftPhotos(reservationId = null) {
  if (currentPhotosStored || !currentDraftId || !selectedPhotos.length) {
    return currentPhotosStored;
  }
  if (!photoStorageAvailable || !photoDatabase || currentUserId === null) {
    photoReselectMessage.textContent =
      "The draft was saved, but browser photo storage is unavailable. Select the originals again before publishing.";
    photoReselectMessage.hidden = false;
    return false;
  }
  const draftId = currentDraftId;
  const draftRevision = currentDraftRevision;
  const revision = photoRevision;
  const photos = [...selectedPhotos];
  try {
    const stats = await photoStorageStats(photoDatabase, currentUserId, draftId);
    const newBytes = selectedPhotoBytes(photos);
    const projectedBytes = stats.totalBytes - stats.currentBytes + newBytes;
    if (projectedBytes > PHOTO_STORAGE_LIMIT) {
      photoReselectMessage.textContent =
        "These photo changes would exceed the 1 GB limit. Delete another draft or remove photos before saving.";
      photoReselectMessage.hidden = false;
      return false;
    }
    await saveDraftPhotos(
      photoDatabase,
      currentUserId,
      draftId,
      photos,
      draftRevision,
      reservationId,
    );
    const storedCurrentSelection = currentDraftId === draftId && photoRevision === revision;
    currentPhotosStored = storedCurrentSelection;
    if (storedCurrentSelection) photoReselectMessage.hidden = true;
    await refreshPhotoStorageDisplay();
    return storedCurrentSelection;
  } catch (error) {
    photoReselectMessage.textContent =
      error.name === "StaleDraftPhotoWriteError"
        ? "A newer version of this draft already saved photos in this browser. Reload the draft before you continue."
        : error.name === "PhotoStorageLimitError"
        ? "These photo changes would exceed the 1 GB limit. Delete another draft or remove photos before saving."
        : "The draft was saved, but this browser could not save its photos. Keep the originals and select them again before publishing.";
    photoReselectMessage.hidden = false;
    return false;
  }
}

async function retryPendingPhotoDeletions() {
  if (!photoDatabase || currentUserId === null) return;
  let pending;
  try {
    pending = await pendingDraftPhotoDeletions(photoDatabase, currentUserId);
  } catch {
    return;
  }
  pending.forEach((entry) => pendingDeletionDraftIds.add(entry.draftId));
  for (const entry of pending) {
    try {
      const response = await fetch(`/api/drafts/${entry.draftId}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) {
        if (response.status >= 500 || [408, 429].includes(response.status)) continue;
        const cleared = await clearDraftPhotoDeletion(
          photoDatabase,
          currentUserId,
          entry.draftId,
          entry.token,
        );
        if (cleared) pendingDeletionDraftIds.delete(entry.draftId);
        continue;
      }
      await deleteDraftPhotos(photoDatabase, currentUserId, entry.draftId, entry.token);
      pendingDeletionDraftIds.delete(entry.draftId);
    } catch {
      // The transactional tombstone keeps this cleanup pending for the next reload.
    }
  }
}

async function draftDeletionIsPending(draftId) {
  if (!photoDatabase || currentUserId === null) return null;
  try {
    const pending = await pendingDraftPhotoDeletions(photoDatabase, currentUserId);
    const deletionPending = pending.some((entry) => entry.draftId === draftId);
    if (deletionPending) pendingDeletionDraftIds.add(draftId);
    else pendingDeletionDraftIds.delete(draftId);
    return deletionPending;
  } catch {
    return null;
  }
}

function showEbayPublishControls() {
  ebayDraftSection.hidden = !currentDraft;
  if (!currentDraft) return false;
  if (currentDraftPublished) {
    sendToEbayButton.disabled = true;
    return false;
  }
  sendToEbayButton.disabled = true;
  if (ebayConnectionState === "loading") {
    ebayTransferStatus.textContent =
      "Checking your eBay connection. Publishing will be available when this check finishes.";
    ebayTransferStatus.hidden = false;
    return false;
  }
  if (ebayConnectionState === "error") {
    ebayTransferStatus.textContent =
      "The eBay connection status is not available. Keep this draft open and reload after it is saved.";
    ebayTransferStatus.hidden = false;
    return false;
  }
  if (!ebayConnection?.configured) {
    ebayTransferStatus.textContent = "eBay setup is not complete on this server.";
    ebayTransferStatus.hidden = false;
    return false;
  }
  if (!ebayConnection.connected) {
    ebayTransferStatus.textContent =
      ebayConnection.status === "expired"
        ? "Reconnect your eBay account before you publish."
        : "Connect your eBay seller account before you publish.";
    ebayTransferStatus.hidden = false;
    return false;
  }
  if (!ebayConnection.direct_publish_enabled) {
    ebayTransferStatus.textContent =
      "Live eBay publication is not enabled on this server. Ask the app owner to enable it.";
    ebayTransferStatus.hidden = false;
    sendToEbayButton.disabled = true;
    updatePublishButton();
    return false;
  }
  ebayTransferStatus.replaceChildren();
  ebayTransferStatus.hidden = true;
  return true;
}

async function responsePayload(response, fallback) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: fallback };
  }
}

async function loadSession() {
  const response = await fetch("/api/session");
  if (!response.ok) {
    window.location.assign("/login");
    return;
  }
  const session = await responsePayload(response, "The account response was not valid.");
  if (!session.authenticated) {
    window.location.assign("/login");
    return;
  }
  accountEmail.textContent = session.user.email;
  currentUserId = session.user.id;
  adminLink.hidden = !session.user.is_owner;
  try {
    photoDatabase = await openPhotoStore();
    await retryPendingPhotoDeletions();
  } catch {
    photoStorageAvailable = false;
  }
  await refreshPhotoStorageDisplay();
  await Promise.all([loadEbayConnection(), loadSavedDrafts(), loadGenerationGroups()]);
}

loadSession();

logoutButton.addEventListener("click", async () => {
  await fetch("/api/session", { method: "DELETE" });
  window.location.assign("/login");
});

savedDraftsLink.addEventListener("click", (event) => {
  event.preventDefault();
  savedDraftsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  savedDraftsSection.focus({ preventScroll: true });
});

async function loadEbayConnection() {
  ebayConnectionState = "loading";
  showEbayPublishControls();
  try {
    const response = await fetch("/api/ebay/connection");
    if (!response.ok) throw new Error();
    ebayConnection = await responsePayload(response, "The eBay connection response was not valid.");
    ebayConnectionState = "ready";
    ebayConnectButton.hidden = ebayConnection.connected || !ebayConnection.configured;
    ebayDisconnectButton.hidden = !ebayConnection.connected;
    if (ebayConnection.connected) {
      ebayConnectionStatus.textContent = `Connected to ${ebayConnection.display_name}.`;
    } else if (ebayConnection.status === "expired") {
      ebayConnectionStatus.textContent = "Your eBay authorization expired. Reconnect to continue.";
      ebayConnectButton.hidden = false;
    } else if (!ebayConnection.configured) {
      ebayConnectionStatus.textContent = "eBay setup is not complete on this server.";
    } else {
      ebayConnectionStatus.textContent = "Connect your seller account to send reviewed drafts.";
    }
    await loadEbayDraftSupport();
    localStorage.removeItem("lastEbayTransfer");
    localStorage.removeItem(LEGACY_LAST_EBAY_TRANSFER_KEY);
    const oauthResult = new URLSearchParams(window.location.search).get("ebay");
    if (oauthResult === "already-connected") {
      showError("That eBay seller account is already connected to the other app user.");
    } else if (oauthResult === "connection-failed") {
      showError("The eBay connection failed. Try again.");
    } else if (oauthResult === "connection-cancelled") {
      showError("The eBay connection was cancelled.");
    }
    if (oauthResult) window.history.replaceState({}, "", "/");
  } catch {
    ebayConnection = null;
    ebayConnectionState = "error";
    ebayConnectButton.hidden = true;
    ebayDisconnectButton.hidden = true;
    ebayConnectionStatus.textContent = "The eBay connection status is not available.";
    showEbayPublishControls();
  }
}

async function loadBusinessPolicies(selectedPolicies = null) {
  const revision = ++ebayPoliciesRevision;
  ebayPoliciesState = "loading";
  ebayPolicyStatus.hidden = true;
  Object.values(policyControls).forEach(({ select, field }) => {
    select.replaceChildren();
    select.setAttribute("aria-describedby", "ebay-policy-status ebay-publish-readiness");
    field.hidden = true;
  });
  updatePublishButton();
  try {
    const response = await fetch("/api/ebay/business-policies");
    const payload = await responsePayload(response, "eBay policies are unavailable.");
    if (!response.ok) throw new Error(payload.detail || "eBay policies are unavailable.");
    if (revision !== ebayPoliciesRevision) return;
    const missing = [];
    Object.entries(policyControls).forEach(([type, control]) => {
      const policies = payload.policies.filter((policy) => policy.type === type);
      const selectedPolicyId = selectedPolicies?.[`${type}_id`];
      if (!policies.length && !selectedPolicyId) {
        missing.push(type.replace("_policy", ""));
        return;
      }
      if (policies.length > 1) {
        control.select.append(new Option("Choose a policy", ""));
      }
      policies.forEach((policy) => control.select.append(new Option(policy.name, policy.id)));
      if (selectedPolicyId && !policies.some((policy) => policy.id === selectedPolicyId)) {
        control.select.append(
          new Option("Saved policy from protected publication", selectedPolicyId),
        );
      }
      if (selectedPolicyId) control.select.value = selectedPolicyId;
      control.field.hidden = policies.length <= 1;
      control.select.disabled = Boolean(selectedPolicies);
    });
    if (missing.length) {
      ebayPolicyStatus.replaceChildren(
        document.createTextNode(`Create a ${missing.join(", ")} policy in `),
      );
      const link = document.createElement("a");
      link.href = "https://www.ebay.com/bp/manage";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Seller Hub";
      ebayPolicyStatus.append(link, document.createTextNode(" before publishing."));
      ebayPolicyStatus.hidden = false;
    }
    ebayPoliciesState = missing.length ? "missing" : "ready";
    updatePublishButton();
  } catch (error) {
    if (revision !== ebayPoliciesRevision) return;
    ebayPoliciesState = "error";
    ebayPolicyStatus.textContent = error.message || "eBay policies are unavailable.";
    ebayPolicyStatus.hidden = false;
    updatePublishButton();
  }
}

function invalidateEbayDraftLoaders() {
  categorySuggestionRevision += 1;
  categoryRequirementsRevision += 1;
  ebayPoliciesRevision += 1;
}

function restoreProtectedPublicationSupport() {
  if (!publicationRecoveryPending) return false;
  ebayCategoryState = "ready";
  ebayRequirementsState = "ready";
  ebayPoliciesState = "ready";
  ebayRequirementsValid = true;
  ebayCategoryRules = null;
  ebayRequirements.replaceChildren();
  ebayRequirements.hidden = true;
  ebayPolicyStatus.replaceChildren();
  ebayPolicyStatus.hidden = true;

  ebayCategory.replaceChildren();
  if (savedEbayCategory) {
    const option = new Option(savedEbayCategory.path, savedEbayCategory.category_id);
    option.dataset.name = savedEbayCategory.name;
    option.dataset.groupId = savedEbayCategory.group_id || "";
    ebayCategory.append(option);
    ebayCategory.value = savedEbayCategory.category_id;
  }
  ebayCategory.disabled = true;

  ebayCondition.replaceChildren();
  if (savedEbayCondition) {
    ebayCondition.append(
      new Option(savedEbayCondition.name, String(savedEbayCondition.condition_id)),
    );
    ebayCondition.value = String(savedEbayCondition.condition_id);
  }
  ebayCondition.disabled = true;

  Object.entries(policyControls).forEach(([type, control]) => {
    const policyId = publicationRecoveryPolicies?.[`${type}_id`];
    control.select.replaceChildren();
    if (policyId) {
      control.select.append(
        new Option("Saved policy from protected publication", policyId),
      );
      control.select.value = policyId;
    }
    control.select.disabled = true;
    control.field.hidden = false;
  });
  updatePublishButton();
  return true;
}

async function loadEbayDraftSupport() {
  if (draftOpenInFlight || !currentDraft || currentDraftPublished) return;
  if (!showEbayPublishControls()) return;
  if (restoreProtectedPublicationSupport()) return;
  if (savedEbayCategory && !ebayCategory.value) restoreDraftCategory();
  await Promise.all([
    savedEbayCategory ? loadCategoryRequirements() : loadCategorySuggestions(),
    loadBusinessPolicies(publicationRecoveryPolicies),
  ]);
}

Object.values(policyControls).forEach(({ select }) => {
  select.addEventListener("change", updatePublishButton);
});

for (const input of [packageWeightPounds, packageWeightOunces]) {
  input.addEventListener("input", () => {
    updatePublishButton();
    scheduleDraftSave();
  });
}

for (const input of [packageLength, packageWidth, packageHeight]) {
  input.addEventListener("input", () => {
    packageDimensionRoundingMessages.delete(input);
    renderPackageDimensionRoundingStatus();
    updatePublishButton();
    scheduleDraftSave();
  });
  input.addEventListener("change", () => {
    normalizePackageDimensions();
    updatePublishButton();
    scheduleDraftSave();
  });
}

async function loadSavedDrafts() {
  const loadRevision = ++savedDraftsLoadRevision;
  if (!savedDraftsList.children.length) {
    savedDraftsStatus.textContent = "Loading saved drafts…";
    savedDraftsStatus.hidden = false;
  }
  try {
    const response = await fetch("/api/drafts");
    const payload = await responsePayload(response, "Saved drafts are not available.");
    if (!response.ok) throw new Error(payload.detail || "Saved drafts are not available.");
    if (loadRevision !== savedDraftsLoadRevision) return;
    if (photoStorageAvailable && photoDatabase && currentUserId !== null) {
      try {
        const pending = await pendingDraftPhotoDeletions(photoDatabase, currentUserId);
        if (loadRevision !== savedDraftsLoadRevision) return;
        pendingDeletionDraftIds.clear();
        pending.forEach((entry) => pendingDeletionDraftIds.add(entry.draftId));
        if (currentDraftId && pendingDeletionDraftIds.has(currentDraftId)) {
          closeCurrentDraftAfterDeletion();
        }
        await refreshPhotoStorageDisplay();
      } catch {
        photoStorageAvailable = false;
        await refreshPhotoStorageDisplay();
      }
    }
    if (loadRevision !== savedDraftsLoadRevision) return;
    savedDraftsList.replaceChildren();
    const visibleDrafts = payload.drafts.filter(
      (draft) => !pendingDeletionDraftIds.has(draft.id),
    );
    visibleDrafts.forEach((draft) => {
      const row = document.createElement("div");
      row.className = "saved-draft-row";
      row.dataset.draftId = draft.id;
      const details = document.createElement("div");
      const title = document.createElement("p");
      title.className = "saved-draft-title";
      title.textContent = draft.title;
      if (draft.status === "published") {
        const badge = document.createElement("span");
        badge.className = "status-badge";
        badge.textContent = "Published";
        title.append(badge);
      }
      const meta = document.createElement("span");
      meta.className = "count";
      meta.textContent = `${draft.photo_count} photo${draft.photo_count === 1 ? "" : "s"} · ${new Date(draft.updated_at).toLocaleDateString()}`;
      details.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "saved-draft-actions";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "secondary-button";
      open.textContent = "Open";
      open.disabled =
        ebayPublishInFlight || generationInFlight || draftDeletionInFlight || draftOpenInFlight;
      open.addEventListener("click", () => openSavedDraft(draft.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "link-button";
      remove.textContent = "Delete";
      remove.disabled =
        ebayPublishInFlight || generationInFlight || draftDeletionInFlight || draftOpenInFlight;
      remove.addEventListener("click", () => deleteSavedDraft(draft.id));
      actions.append(open, remove);
      row.append(details, actions);
      savedDraftsList.append(row);
    });
    savedDraftsCount.textContent = ` (${visibleDrafts.length})`;
    savedDraftsList.hidden = visibleDrafts.length === 0;
    savedDraftsStatus.textContent = visibleDrafts.length
      ? ""
      : "No saved drafts yet. Generate a listing and it will appear here.";
    savedDraftsStatus.hidden = visibleDrafts.length > 0;
  } catch {
    if (loadRevision !== savedDraftsLoadRevision) return;
    savedDraftsStatus.textContent = savedDraftsList.children.length
      ? "We could not refresh saved drafts. The list below might be out of date."
      : "Saved drafts are temporarily unavailable. Reload the page to try again.";
    savedDraftsStatus.hidden = false;
  }
}

function setSavedDraftActionsDisabled(disabled) {
  savedDraftsList.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function freezeDraftOpenControls() {
  [
    input,
    notes,
    ...generationCategoryControls,
    generateButton,
    ...photoList.querySelectorAll("button"),
    ...resultSection.querySelectorAll("input, textarea, select, button"),
    ...savedDraftsList.querySelectorAll("button"),
  ].forEach((control) => {
    if (!draftOpenDisabledControls.has(control)) {
      draftOpenDisabledControls.set(control, control.disabled);
    }
    control.disabled = true;
  });
}

function restoreDraftOpenControls() {
  draftOpenDisabledControls.forEach((disabled, control) => {
    control.disabled = disabled;
  });
  draftOpenDisabledControls.clear();
}

async function openSavedDraft(id) {
  clearError();
  if (draftOpenInFlight) {
    showError("Wait for the current saved draft to finish opening.");
    return;
  }
  if (draftDeletionInFlight) {
    showError("Wait for draft deletion to finish before you open another draft.");
    return;
  }
  if (ebayPublishInFlight) {
    showError("Wait for eBay publication to finish before you open another draft.");
    return;
  }
  if (generationInFlight) {
    showError("Wait for generation to finish before you open another draft.");
    return;
  }
  const openRevision = ++draftOpenRevision;
  draftOpenInFlight = true;
  generationCategorySearchRevision += 1;
  invalidateEbayDraftLoaders();
  freezeDraftOpenControls();
  try {
    if (await draftDeletionIsPending(id)) {
      hidePendingDeletedDraft(id);
      showError("This draft is being deleted in another tab. Wait for cleanup to finish.");
      return;
    }
    if (openRevision !== draftOpenRevision) return;
    if (currentDraftId && !currentDraftSaveIsNoOp() && !(await saveCurrentDraft())) {
      showError("Save the current draft before you open another one.");
      return;
    }
    if (openRevision !== draftOpenRevision) return;
    const response = await fetch(`/api/drafts/${id}`);
    const payload = await responsePayload(response, "We could not open that saved draft.");
    if (openRevision !== draftOpenRevision) return;
    if (!response.ok) {
      showError(payload.detail || "We could not open that saved draft.");
      return;
    }
    if (await draftDeletionIsPending(id)) {
      hidePendingDeletedDraft(id);
      showError("This draft is being deleted in another tab. Wait for cleanup to finish.");
      return;
    }
    if (openRevision !== draftOpenRevision) return;
    let openedPhotos = [];
    if (photoDatabase && currentUserId !== null) {
      try {
        openedPhotos = await loadDraftPhotos(
          photoDatabase,
          currentUserId,
          id,
          payload.revision,
        );
        if (openRevision !== draftOpenRevision) return;
      } catch {
        photoStorageAvailable = false;
      }
    }
    if (openRevision !== draftOpenRevision) return;
    restoreDraftOpenControls();
    selectedPhotos = openedPhotos;
    currentPhotosStored = selectedPhotos.length === payload.photo_count;
    photoRevision += 1;
    photoRecoveryExpectedCount = currentPhotosStored ? null : payload.photo_count;
    renderPhotos();
    currentPhotoCount = payload.photo_count;
    savedEbayCategory = payload.ebay_category;
    savedEbayCondition = payload.ebay_condition;
    renderDraft(
      { ...payload, draft_id: payload.id, saved: true, warnings: [] },
      true,
      { deferEbayLoads: true },
    );
    freezeDraftOpenControls();
    photoReselectMessage.hidden = selectedPhotos.length === payload.photo_count;
    await refreshPhotoStorageDisplay();
    if (openRevision !== draftOpenRevision) return;
  } finally {
    if (openRevision === draftOpenRevision) {
      restoreDraftOpenControls();
      draftOpenInFlight = false;
      updateGenerationReadiness();
      setSavedDraftActionsDisabled(
        ebayPublishInFlight || generationInFlight || draftDeletionInFlight,
      );
      void loadEbayDraftSupport();
    }
  }
}

function closeCurrentDraftAfterDeletion() {
  currentDraft = null;
  currentDraftId = null;
  currentDraftPublished = false;
  publicationRecoveryPending = false;
  publicationRecoveryPolicies = null;
  publicationRecoveryPreparationId = null;
  currentPhotosStored = false;
  selectedPhotos = [];
  currentPhotoCount = 0;
  photoRecoveryExpectedCount = null;
  input.disabled = false;
  input.value = "";
  notes.disabled = false;
  notes.value = "";
  restoreGenerationCategory(null);
  startNewListingButton.hidden = true;
  publishedDraftMessage.hidden = true;
  renderPhotos();
  resultSection.hidden = true;
}

function hidePendingDeletedDraft(id) {
  Array.from(savedDraftsList.querySelectorAll(".saved-draft-row"))
    .find((row) => row.dataset.draftId === id)
    ?.remove();
  const remaining = savedDraftsList.querySelectorAll(".saved-draft-row").length;
  savedDraftsCount.textContent = ` (${remaining})`;
  savedDraftsList.hidden = remaining === 0;
  savedDraftsStatus.textContent = remaining ? "" : "No saved drafts available.";
  savedDraftsStatus.hidden = remaining > 0;
}

async function deleteSavedDraft(id) {
  if (draftDeletionInFlight) {
    showError("Wait for the current draft deletion to finish.");
    return;
  }
  if (ebayPublishInFlight) {
    showError("Wait for eBay publication to finish before you delete a draft.");
    return;
  }
  if (generationInFlight) {
    showError("Wait for generation to finish before you delete a draft.");
    return;
  }
  if (!window.confirm("Delete this saved draft? This cannot be undone.")) return;
  draftDeletionInFlight = true;
  generationCategorySearchRevision += 1;
  const deletingCurrent = currentDraftId === id;
  const autosaveWasPending = deletingCurrent && saveTimer !== null;
  const deletionDisabledControls = new Map();
  const restoreDeletionControls = () => {
    deletionDisabledControls.forEach((disabled, control) => {
      control.disabled = disabled;
    });
    deletionDisabledControls.clear();
  };
  const finishDeletion = () => {
    draftDeletionInFlight = false;
    updateGenerationReadiness();
    setSavedDraftActionsDisabled(ebayPublishInFlight || generationInFlight);
  };
  const restoreCurrentDraft = () => {
    currentDraftId = id;
    restoreDeletionControls();
    if (autosaveWasPending && savedRevision < saveRevision) {
      draftSaveStatus.textContent = "Saving…";
      saveDraftButton.hidden = true;
      saveTimer = setTimeout(saveCurrentDraft, 600);
    }
  };
  if (deletingCurrent) {
    clearTimeout(saveTimer);
    saveTimer = null;
    currentDraftId = null;
    [
      input,
      notes,
      ...generationCategoryControls,
      generateButton,
      startNewListingButton,
      ...resultSection.querySelectorAll("input, textarea, select, button"),
      ...savedDraftsList.querySelectorAll("button"),
    ].forEach((control) => {
      if (!deletionDisabledControls.has(control)) {
        deletionDisabledControls.set(control, control.disabled);
      }
      control.disabled = true;
    });
    if (saveInFlight) {
      try {
        await saveInFlight;
      } catch {
        // Deletion remains available when a save failed.
      }
    }
  }
  const deletion = {
    accountId: currentUserId,
    draftId: id,
    token: crypto.randomUUID(),
  };
  let localDeletionProtected =
    photoStorageAvailable && currentUserId !== null && Boolean(photoDatabase);
  if (localDeletionProtected) {
    try {
      await markDraftPhotoDeletion(photoDatabase, currentUserId, id, deletion.token);
      pendingDeletionDraftIds.add(id);
    } catch {
      photoStorageAvailable = false;
      localDeletionProtected = false;
    }
  }
  let response;
  try {
    response = await fetch(`/api/drafts/${id}`, { method: "DELETE" });
  } catch {
    if (deletingCurrent) closeCurrentDraftAfterDeletion();
    hidePendingDeletedDraft(id);
    await refreshPhotoStorageDisplay();
    finishDeletion();
    showError(
      localDeletionProtected
        ? "The deletion result is uncertain. This browser will retry the draft and photo cleanup later."
        : "The deletion result is uncertain. Reload to check the server draft before you try again.",
    );
    return;
  }
  if (!response.ok && response.status !== 404) {
    const payload = await responsePayload(response, "We could not delete that saved draft.");
    if (response.status >= 500 || [408, 429].includes(response.status)) {
      if (!localDeletionProtected) {
        if (deletingCurrent) {
          restoreCurrentDraft();
        }
        finishDeletion();
        showError(payload.detail || "The draft was not deleted. Try again.");
        return;
      }
      if (deletingCurrent) closeCurrentDraftAfterDeletion();
      hidePendingDeletedDraft(id);
      await refreshPhotoStorageDisplay();
      finishDeletion();
      showError(
        `${payload.detail || "The deletion result is uncertain."} ` +
          "This browser will retry the draft and photo cleanup later.",
      );
      return;
    }
    if (localDeletionProtected && photoDatabase) {
      try {
        const cleared = await clearDraftPhotoDeletion(
          photoDatabase,
          currentUserId,
          id,
          deletion.token,
        );
        if (!cleared) {
          if (deletingCurrent) closeCurrentDraftAfterDeletion();
          hidePendingDeletedDraft(id);
          finishDeletion();
          showError(
            "Another deletion attempt is still pending. This browser will retry cleanup later.",
          );
          return;
        }
        pendingDeletionDraftIds.delete(id);
      } catch {
        if (deletingCurrent) closeCurrentDraftAfterDeletion();
        hidePendingDeletedDraft(id);
        finishDeletion();
        showError(
          "The draft was not deleted, but this browser could not restore its photo state. Reload and try again.",
        );
        return;
      }
    }
    if (deletingCurrent) {
      restoreCurrentDraft();
    }
    finishDeletion();
    showError(payload.detail || "We could not delete that saved draft.");
    return;
  }
  if (photoDatabase && currentUserId !== null) {
    try {
      await deleteDraftPhotos(photoDatabase, currentUserId, id, deletion.token);
      pendingDeletionDraftIds.delete(id);
    } catch {
      showError(
        "The draft was deleted, but this browser could not remove its local photos. It will try again later.",
      );
    }
  }
  if (deletingCurrent) {
    closeCurrentDraftAfterDeletion();
  }
  finishDeletion();
  await loadSavedDrafts();
  await refreshPhotoStorageDisplay();
  if (!localDeletionProtected) {
    showError(
      "The server draft was deleted. Browser photo storage was unavailable, so clear this site's browser data if you must remove a possible local copy.",
    );
  }
}

ebayConnectButton.addEventListener("click", async () => {
  clearError();
  const response = await fetch("/api/ebay/oauth/start", { method: "POST" });
  const payload = await responsePayload(response, "The eBay connection could not start.");
  if (!response.ok) {
    showError(payload.detail || "The eBay connection could not start.");
    return;
  }
  window.location.assign(payload.authorization_url);
});

ebayDisconnectButton.addEventListener("click", async () => {
  const response = await fetch("/api/ebay/connection", { method: "DELETE" });
  if (response.ok) {
    ebayConnection = { ...ebayConnection, connected: false, status: "disconnected" };
    ebayConnectionStatus.textContent = "eBay is disconnected.";
    ebayConnectButton.hidden = false;
    ebayDisconnectButton.hidden = true;
    ebayCategoryRules = null;
    ebayRequirementsState = "idle";
    ebayRequirementsValid = false;
    showEbayPublishControls();
    setFreeSpecificEditing(true);
  }
});

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}

function clearError() {
  errorMessage.textContent = "";
  errorMessage.hidden = true;
}

function photoChangeIsBlocked() {
  if (draftOpenInFlight) {
    showError("Wait for the current saved draft to finish opening.");
    return true;
  }
  if (draftDeletionInFlight) {
    showError("Wait for draft deletion to finish before you change photos.");
    return true;
  }
  if (ebayPublishInFlight || generationInFlight) {
    showError("Wait for the current listing action to finish before you change photos.");
    return true;
  }
  if (currentDraftPublished) {
    showError("This draft is published and its photos are read-only.");
    return true;
  }
  return false;
}

async function addPhotos(files) {
  clearError();
  if (photoChangeIsBlocked()) return;
  const originatingDraftId = currentDraftId;
  const originatingPhotoRevision = photoRevision;
  const incoming = Array.from(files);
  if (selectedPhotos.length + incoming.length > MAX_PHOTOS) {
    showError(`Choose no more than ${MAX_PHOTOS} photos.`);
    return;
  }
  const oversized = incoming.find((file) => file.size > MAX_FILE_BYTES);
  if (oversized) {
    showError(`${oversized.name} is larger than 12 MB. Choose a smaller photo.`);
    return;
  }
  if (currentDraftId && photoDatabase && currentUserId !== null) {
    try {
      const stats = await photoStorageStats(photoDatabase, currentUserId, currentDraftId);
      const newBytes = selectedPhotoBytes([...selectedPhotos, ...incoming]);
      const projectedBytes = stats.totalBytes - stats.currentBytes + newBytes;
      if (projectedBytes > PHOTO_STORAGE_LIMIT) {
        showError(
          "These photos would exceed the 1 GB draft photo limit in this browser. Delete drafts or remove photos before adding them.",
        );
        return;
      }
      const addedBytes = Math.max(0, newBytes - stats.currentBytes);
      if (!(await browserHasRoomFor(addedBytes))) {
        showError(
          "This browser does not have enough available device storage for these photos. Delete drafts or free device storage before you continue.",
        );
        return;
      }
    } catch {
      // IndexedDB reports any later write failure without losing the server draft.
    }
  }
  if (photoChangeIsBlocked()) return;
  if (
    currentDraftId !== originatingDraftId ||
    photoRevision !== originatingPhotoRevision
  ) {
    showError(
      "The open draft changed before these photos were ready. Select the photos again for the draft that is now open.",
    );
    return;
  }
  selectedPhotos.push(...incoming);
  selectedPhotosChanged();
  renderPhotos();
}

function selectedPhotosChanged() {
  photoRevision += 1;
  currentPhotosStored = false;
  if (photoRecoveryExpectedCount !== null) {
    if (selectedPhotos.length !== photoRecoveryExpectedCount) {
      photoReselectMessage.textContent =
        `Select all ${photoRecoveryExpectedCount} original photos before this draft can be updated or published.`;
      photoReselectMessage.hidden = false;
      updatePublishButton();
      return;
    }
    photoRecoveryExpectedCount = null;
    photoReselectMessage.hidden = true;
  }
  if (currentDraftId && !currentDraftPublished && selectedPhotos.length) {
    currentPhotoCount = selectedPhotos.length;
    scheduleDraftSave();
  }
  updatePublishButton();
}

function movePhoto(from, to) {
  if (ebayPublishInFlight || generationInFlight) return;
  if (to < 0 || to >= selectedPhotos.length) return;
  const [photo] = selectedPhotos.splice(from, 1);
  selectedPhotos.splice(to, 0, photo);
  selectedPhotosChanged();
  renderPhotos();
}

function renderPhotos() {
  updateGenerationReadiness();
  photoList.replaceChildren();
  photoCount.textContent = `${selectedPhotos.length} / ${MAX_PHOTOS}`;

  selectedPhotos.forEach((file, index) => {
    const card = document.createElement("article");
    card.className = "photo-card";

    const objectUrl = URL.createObjectURL(file);
    const image = document.createElement("img");
    image.className = "photo-preview";
    image.alt = `Selected photo ${index + 1}: ${file.name}`;
    image.src = objectUrl;
    image.addEventListener("load", () => URL.revokeObjectURL(objectUrl), { once: true });
    image.addEventListener(
      "error",
      () => {
        URL.revokeObjectURL(objectUrl);
        const fallback = document.createElement("div");
        fallback.className = "photo-preview";
        fallback.textContent = file.name;
        image.replaceWith(fallback);
      },
      { once: true },
    );
    card.append(image);

    if (index === 0) {
      const badge = document.createElement("span");
      badge.className = "main-badge";
      badge.textContent = "Main photo";
      card.append(badge);
    }

    const actions = document.createElement("div");
    actions.className = "photo-actions";
    const previous = actionButton("←", `Move photo ${index + 1} earlier`, index === 0, () =>
      movePhoto(index, index - 1),
    );
    previous.disabled ||=
      currentDraftPublished || ebayPublishInFlight || generationInFlight;
    const remove = actionButton(
      "Remove",
      `Remove photo ${index + 1}`,
      currentDraftPublished ||
        ebayPublishInFlight ||
        generationInFlight ||
        Boolean(currentDraftId && selectedPhotos.length === 1),
      () => {
      selectedPhotos.splice(index, 1);
      selectedPhotosChanged();
      renderPhotos();
      },
    );
    const next = actionButton(
      "→",
      `Move photo ${index + 1} later`,
      index === selectedPhotos.length - 1,
      () => movePhoto(index, index + 1),
    );
    next.disabled ||=
      currentDraftPublished || ebayPublishInFlight || generationInFlight;
    actions.append(previous, remove, next);
    card.append(actions);
    photoList.append(card);
  });
  refreshPhotoStorageDisplay();
}

function actionButton(text, label, disabled, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = text;
  button.ariaLabel = label;
  button.disabled = disabled;
  button.addEventListener("click", action);
  return button;
}

async function readPhotoInput() {
  const files = Array.from(input.files);
  input.value = "";
  await addPhotos(files);
}

input.addEventListener("change", readPhotoInput);
// The file control can be used before this module and its imports finish loading.
if (input.files.length) void readPhotoInput();

for (const eventName of ["dragenter", "dragover"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("is-dragging");
  });
}

for (const eventName of ["dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
  });
}

dropZone.addEventListener("drop", (event) => addPhotos(event.dataTransfer.files));

function startNewListing() {
  clearTimeout(saveTimer);
  saveTimer = null;
  currentDraft = null;
  currentDraftId = null;
  currentDraftRevision = null;
  currentDraftPublished = false;
  publicationRecoveryPending = false;
  currentPhotosStored = false;
  currentPhotoCount = 0;
  photoRecoveryExpectedCount = null;
  selectedPhotos = [];
  input.disabled = false;
  input.value = "";
  notes.disabled = false;
  notes.value = "";
  startNewListingButton.hidden = true;
  restoreGenerationCategory(null);
  resultSection.querySelectorAll("input, textarea, select, button").forEach((control) => {
    control.disabled = false;
  });
  publishedDraftMessage.hidden = true;
  resultSection.hidden = true;
  clearError();
  renderPhotos();
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

startNewListingButton.addEventListener("click", startNewListing);

function setLoading(loading) {
  updateGenerationReadiness();
  buttonLabel.hidden = loading;
  buttonLoading.hidden = !loading;
}

function finishGeneration() {
  generationInFlight = false;
  generationDisabledControls.forEach((disabled, control) => {
    control.disabled = disabled;
  });
  generationDisabledControls.clear();
  renderPhotos();
  setSavedDraftActionsDisabled(ebayPublishInFlight);
  setLoading(false);
}

function freezeGenerationControls() {
  const controls = [
    input,
    notes,
    ...generationCategoryControls,
    ...photoList.querySelectorAll("button"),
    ...resultSection.querySelectorAll("input, textarea, select, button"),
  ];
  controls.forEach((control) => {
    generationDisabledControls.set(control, control.disabled);
    control.disabled = true;
  });
  setSavedDraftActionsDisabled(true);
}

function addField(label, value, options = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = `draft-field${options.wide ? " wide" : ""}`;
  const id = `draft-${draftFields.children.length}`;
  const fieldLabel = document.createElement("label");
  fieldLabel.htmlFor = id;
  fieldLabel.textContent = label;
  const field = options.multiline ? document.createElement("textarea") : document.createElement("input");
  field.id = id;
  field.value = value ?? "";
  field.dataset.copyLabel = label;
  if (options.key) field.dataset.draftKey = options.key;
  if (options.multiline) field.rows = options.rows ?? 3;
  wrapper.append(fieldLabel, field);
  draftFields.append(wrapper);
  return wrapper;
}

function renderDraft(payload, reopened = false, options = {}) {
  // New draft controls must not inherit the prior draft's publication recovery locks.
  if (generationInFlight && !reopened) {
    for (const control of generationDisabledControls.keys()) {
      if (resultSection.contains(control)) generationDisabledControls.delete(control);
    }
  }
  invalidateEbayDraftLoaders();
  const draft = payload.draft;
  reconcileNewGeneration = !reopened;
  currentDraft = draft;
  currentDraftPublished = payload.status === "published";
  publicationRecoveryPending = Boolean(payload.publication_recovery_pending);
  publicationRecoveryPolicies = payload.publication_recovery_policies || null;
  publicationRecoveryPreparationId = payload.publication_recovery_preparation_id || null;
  input.disabled = currentDraftPublished;
  notes.disabled = currentDraftPublished;
  updateGenerationReadiness();
  startNewListingButton.hidden = !currentDraftPublished;
  if (!currentDraftPublished) {
    resultSection.querySelectorAll("input, textarea, select, button").forEach((control) => {
      control.disabled = false;
    });
  }
  ebayCategoryRules = null;
  ebayCategoryState = "idle";
  ebayRequirementsState = "idle";
  ebayPoliciesState = "idle";
  ebayRequirementsValid = false;
  ebayPublishInFlight = false;
  packageWeightPounds.value = String(draft.package_weight?.pounds ?? 0);
  packageWeightOunces.value = String(draft.package_weight?.ounces ?? 0);
  packageLength.value = String(draft.package_dimensions?.length ?? "");
  packageWidth.value = String(draft.package_dimensions?.width ?? "");
  packageHeight.value = String(draft.package_dimensions?.height ?? "");
  currentDraftId = payload.draft_id;
  currentDraftRevision = payload.draft_revision || payload.revision || null;
  if (!reopened) {
    currentPhotosStored = false;
    currentPhotoCount = selectedPhotos.length;
    photoRecoveryExpectedCount = null;
    savedEbayCategory = payload.ebay_category || null;
    savedEbayCondition = null;
  }
  restoreGenerationCategory(payload.ebay_category || savedEbayCategory);

  restoreDraftCategory();
  draftFields.replaceChildren();
  addField("Title", draft.title, { wide: true, key: "title" });
  addField("Suggested category", draft.suggested_category, { key: "suggested_category" });
  addField("Search terms", draft.search_terms.join(", "), { key: "search_terms" });
  addField("Condition", draft.condition, { key: "condition" });
  addField("Listing format", draft.recommended_listing_format.replace("_", " "), {
    key: "recommended_listing_format",
  });
  addField("Condition description", draft.condition_description, {
    multiline: true,
    wide: true,
    key: "condition_description",
  });
  addField("Description", draft.description, {
    multiline: true,
    wide: true,
    rows: 7,
    key: "description",
  });

  const specifics = document.createElement("div");
  specifics.className = "draft-field wide";
  const heading = document.createElement("span");
  heading.className = "specific-heading";
  heading.textContent = "Item specifics";
  const grid = document.createElement("div");
  grid.className = "specifics-grid";
  draft.item_specifics.forEach((specific, index) => {
    addSpecificRow(grid, specific, index);
  });
  const specificPicker = document.createElement("select");
  specificPicker.dataset.specificPicker = "true";
  specificPicker.ariaLabel = "Add item specific";
  specificPicker.disabled = true;
  specificPicker.replaceChildren(new Option("Choose an eBay category first", ""));
  specificPicker.addEventListener("change", () => {
    const aspect = ebayCategoryRules?.aspects.find(
      (item) => item.name === specificPicker.value,
    );
    if (!aspect) return;
    addSpecificRow(
      grid,
      { name: aspect.name, values: [], source: "seller_note", confidence: "high" },
      grid.children.length,
    );
    applyAspectChoices();
    renderRequirements();
    grid.lastElementChild.querySelector("[data-specific-value]").focus();
  });
  const customSpecific = document.createElement("button");
  customSpecific.type = "button";
  customSpecific.className = "link-button";
  customSpecific.dataset.customSpecific = "true";
  customSpecific.textContent = "Add custom item specific";
  customSpecific.hidden = showEbayPublishControls();
  customSpecific.addEventListener("click", () => {
    addSpecificRow(
      grid,
      { name: "", values: [], source: "seller_note", confidence: "high" },
      grid.children.length,
    );
    setFreeSpecificEditing(true);
    grid.lastElementChild.querySelector("[data-specific-name]").focus();
  });
  specificPicker.hidden = !showEbayPublishControls();
  specifics.append(heading, grid, specificPicker, customSpecific);
  draftFields.append(specifics);
  if (!showEbayPublishControls()) setFreeSpecificEditing(true);

  addField("Quantity", String(draft.quantity), { key: "quantity" });
  addField("Suggested price (USD)", draft.pricing.suggested_price.toFixed(2), { key: "price" });
  addField("Expected sale low (USD)", draft.pricing.expected_sale_price_low.toFixed(2), {
    key: "expected_sale_price_low",
  });
  addField("Expected sale high (USD)", draft.pricing.expected_sale_price_high.toFixed(2), {
    key: "expected_sale_price_high",
  });
  addField("Pricing confidence", draft.pricing.confidence, { key: "pricing_confidence" });
  addField("Pricing rationale", draft.pricing.rationale, {
    multiline: true,
    wide: true,
    key: "pricing_rationale",
  });
  addField("Observed flaws", draft.observed_flaws.join("\n") || "None clearly visible", {
    multiline: true,
    wide: true,
    key: "observed_flaws",
  });
  addField("Missing facts to review", draft.missing_facts.join("\n") || "None", {
    multiline: true,
    wide: true,
    key: "missing_facts",
  });

  if (payload.warnings.length) {
    warningList.textContent = payload.warnings
      .map((warning) => `Photo ${warning.photo_number}: ${warning.message}`)
      .join(" ");
    warningList.hidden = false;
  } else {
    warningList.hidden = true;
  }

  resultSection.hidden = false;
  photoReselectMessage.hidden = !reopened || selectedPhotos.length === payload.photo_count;
  draftSaveStatus.textContent = currentDraftPublished
    ? "Published"
    : payload.saved
      ? "Saved"
      : "Not saved";
  saveDraftButton.hidden = payload.saved || currentDraftPublished;
  draftSaveMessage.textContent = payload.saved
    ? ""
    : "This draft is not saved. Keep this page open and select Save now. Use Copy listing for a backup if saving still fails.";
  draftSaveMessage.hidden = payload.saved || currentDraftPublished;
  saveRevision = 0;
  savedRevision = payload.saved ? 0 : -1;
  ebayTransferStatus.replaceChildren();
  ebayTransferStatus.hidden = true;
  ebayPublishReadiness.replaceChildren();
  ebayPublishReadiness.hidden = true;
  packageDimensionRoundingMessages.clear();
  packageDimensionsStatus.replaceChildren();
  packageDimensionsStatus.hidden = true;
  sendToEbayButton.textContent = publicationRecoveryPending
    ? "Retry publish"
    : "Publish live on eBay";
  publishedDraftMessage.replaceChildren();
  publishedDraftMessage.hidden = !currentDraftPublished;
  if (currentDraftPublished) {
    publishedDraftMessage.append(
      document.createTextNode(
        "This draft is published and read-only. It and its device-local photos remain here until you delete the draft. ",
      ),
    );
    if (payload.listing_url) {
      const listing = document.createElement("a");
      listing.href = payload.listing_url;
      listing.target = "_blank";
      listing.rel = "noopener";
      listing.textContent = "View listing";
      publishedDraftMessage.append(listing, document.createTextNode(" or "));
      const sellerHub = document.createElement("a");
      sellerHub.href = payload.seller_hub_url;
      sellerHub.target = "_blank";
      sellerHub.rel = "noopener";
      sellerHub.textContent = "edit it in Seller Hub";
      publishedDraftMessage.append(sellerHub, document.createTextNode("."));
    }
    resultSection.querySelectorAll("input, textarea, select, button").forEach((control) => {
      control.disabled = true;
    });
    copyButton.disabled = false;
    startNewListingButton.disabled = false;
    sendToEbayButton.textContent = "Published";
  } else {
    Object.values(policyControls).forEach(({ select }) => {
      select.disabled = publicationRecoveryPending;
    });
    if (!options.deferEbayLoads) void loadEbayDraftSupport();
  }
  renderPhotos();
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function addSpecificRow(grid, specific, index) {
  const row = document.createElement("div");
  row.className = "specific-row";
  const name = document.createElement("input");
  name.value = specific.name;
  name.ariaLabel = `Item specific ${index + 1} name`;
  name.dataset.specificName = "true";
  name.readOnly = showEbayPublishControls();
  const valueArea = document.createElement("div");
  valueArea.className = "specific-values";
  valueArea.dataset.specificValues = "true";
  const values = specific.values ?? (specific.value ? [specific.value] : []);
  (values.length ? values : [""]).forEach((value, valueIndex) => {
    addSpecificValueInput(row, valueArea, value, index, valueIndex, specific.name);
  });
  const addValue = document.createElement("button");
  addValue.type = "button";
  addValue.className = "link-button specific-add-value";
  addValue.textContent = "Add another value";
  addValue.hidden = true;
  addValue.addEventListener("click", () => {
    addSpecificValueInput(row, valueArea, "", index, valueArea.children.length);
    reindexSpecificRows();
    if (ebayCategoryRules) {
      applyAspectChoices();
      renderRequirements();
    } else {
      setFreeSpecificEditing(true);
    }
    valueArea.lastElementChild.querySelector("input").focus();
    scheduleDraftSave();
  });
  row.dataset.addSpecificValue = "true";
  const confidence = document.createElement("div");
  confidence.className = "confidence";
  confidence.textContent = `${specific.source.replace("_", " ")} · ${specific.confidence} confidence`;
  row.dataset.source = specific.source;
  row.dataset.confidence = specific.confidence;
  name.addEventListener("input", () => {
    row.dataset.sellerEdited = "true";
    row.querySelectorAll("[data-specific-value]").forEach((value) => {
      value.dataset.copyLabel = name.value.trim() || "Item specific";
    });
    scheduleDraftSave();
  });
  const issue = document.createElement("div");
  issue.className = "specific-error";
  issue.dataset.specificError = "true";
  issue.hidden = true;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "link-button";
  remove.dataset.specificRemove = "true";
  remove.textContent = "Remove";
  remove.ariaLabel = `Remove item specific ${index + 1}`;
  remove.addEventListener("click", () => {
    row.remove();
    reindexSpecificRows();
    if (ebayCategoryRules) {
      applyAspectChoices();
      renderRequirements();
    }
    scheduleDraftSave();
  });
  row.append(name, valueArea, addValue, confidence, issue, remove);
  grid.append(row);
  reindexSpecificRows();
}

function addSpecificValueInput(
  row,
  valueArea,
  initialValue,
  specificIndex,
  valueIndex,
  specificName = "",
) {
  const wrapper = document.createElement("div");
  wrapper.className = "specific-value-row";
  const value = document.createElement("input");
  value.value = initialValue;
  value.ariaLabel =
    valueIndex === 0
      ? `Item specific ${specificIndex + 1} value`
      : `Item specific ${specificIndex + 1} value ${valueIndex + 1}`;
  value.dataset.copyLabel =
    row.querySelector("[data-specific-name]")?.value || specificName || "Item specific";
  value.dataset.specificValue = "true";
  value.addEventListener("input", () => {
    row.dataset.sellerEdited = "true";
    if (row.dataset.sellerAttributionPending === "true" && value.value.trim()) {
      row.dataset.source = "seller_note";
      row.dataset.confidence = "high";
      delete row.dataset.sellerAttributionPending;
      row.querySelector(".confidence").textContent = "seller note · high confidence";
    }
    if (ebayCategoryRules) {
      applyAspectChoices();
      renderRequirements();
    }
  });
  const length = document.createElement("span");
  length.className = "specific-length";
  length.dataset.specificLength = "true";
  length.hidden = true;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "link-button specific-remove-value";
  remove.textContent = "Remove value";
  remove.ariaLabel = `Delete value ${valueIndex + 1} from item specific ${specificIndex + 1}`;
  remove.addEventListener("click", () => {
    wrapper.remove();
    if (!valueArea.children.length) {
      addSpecificValueInput(row, valueArea, "", specificIndex, 0);
    }
    reindexSpecificRows();
    if (ebayCategoryRules) {
      applyAspectChoices();
      renderRequirements();
    }
    scheduleDraftSave();
  });
  wrapper.append(value, length, remove);
  valueArea.append(wrapper);
}

function reindexSpecificRows() {
  const grid = draftFields.querySelector(".specifics-grid");
  if (!grid) return;
  Array.from(grid.querySelectorAll(".specific-row")).forEach((row, specificIndex) => {
    const itemNumber = specificIndex + 1;
    row.querySelector("[data-specific-name]").ariaLabel =
      `Item specific ${itemNumber} name`;
    row.querySelector("[data-specific-remove]").ariaLabel =
      `Remove item specific ${itemNumber}`;
    row.querySelectorAll(".specific-value-row").forEach((valueRow, valueIndex) => {
      valueRow.querySelector("[data-specific-value]").ariaLabel =
        valueIndex === 0
          ? `Item specific ${itemNumber} value`
          : `Item specific ${itemNumber} value ${valueIndex + 1}`;
      valueRow.querySelector(".specific-remove-value").ariaLabel =
        `Delete value ${valueIndex + 1} from item specific ${itemNumber}`;
    });
  });
}

async function loadCategorySuggestions() {
  const draftId = currentDraftId;
  const loadRevision = ++categorySuggestionRevision;
  categoryRequirementsRevision += 1;
  setFreeSpecificEditing(false);
  ebayCategoryState = "loading";
  ebayRequirementsState = "idle";
  ebayCategoryRules = null;
  ebayCategory.replaceChildren(new Option("Finding the best category…", ""));
  ebayCategory.disabled = true;
  updatePublishButton();
  try {
    const query = `${draftValue("title")} ${currentDraft.suggested_category}`;
    const response = await fetch(`/api/ebay/categories?q=${encodeURIComponent(query)}`);
    const payload = await responsePayload(response, "eBay category search failed.");
    if (!response.ok) throw new Error(payload.detail);
    if (
      currentDraftId !== draftId ||
      currentDraftPublished ||
      categorySuggestionRevision !== loadRevision
    ) {
      return;
    }
    ebayCategory.replaceChildren(new Option("Choose a category", ""));
    payload.categories.forEach((category) => {
      const option = new Option(category.path, category.category_id);
      option.dataset.name = category.name;
      ebayCategory.append(option);
    });
    if (
      savedEbayCategory &&
      !payload.categories.some((category) => category.category_id === savedEbayCategory.category_id)
    ) {
      const option = new Option(savedEbayCategory.path, savedEbayCategory.category_id);
      option.dataset.name = savedEbayCategory.name;
    option.dataset.groupId = savedEbayCategory.group_id || "";
      ebayCategory.append(option);
    }
    ebayCategory.disabled = false;
    ebayCategoryState = "ready";
    if (payload.categories.length || savedEbayCategory) {
      ebayCategory.value = savedEbayCategory?.category_id || "";
      await loadCategoryRequirements();
    } else {
      setFreeSpecificEditing(true);
      updatePublishButton();
    }
  } catch (error) {
    if (
      currentDraftId !== draftId ||
      currentDraftPublished ||
      categorySuggestionRevision !== loadRevision
    ) {
      return;
    }
    ebayCategoryState = "error";
    ebayRequirementsState = "idle";
    ebayCategoryRules = null;
    ebayCategory.replaceChildren(new Option("Category search is unavailable", ""));
    ebayRequirements.textContent = error.message || "eBay category search failed.";
    ebayRequirements.hidden = false;
    setFreeSpecificEditing(true);
    updatePublishButton();
  }
}

ebayCategory.addEventListener("change", () => {
  markCategoryChange(ebayCategory.value, savedEbayCategory?.category_id || "");
  reconcileNewGeneration = false;
  savedEbayCondition = null;
  restoreGenerationCategory(buildSavedDraftUpdate().ebay_category);
  scheduleDraftSave();
  loadCategoryRequirements();
});

async function loadCategoryRequirements() {
  const draftId = currentDraftId;
  const categoryId = ebayCategory.value;
  const loadRevision = ++categoryRequirementsRevision;
  setFreeSpecificEditing(false);
  ebayRequirementsValid = false;
  ebayRequirementsState = "loading";
  ebayCategoryRules = null;
  ebayCondition.replaceChildren(new Option("Loading conditions…", ""));
  ebayCondition.disabled = true;
  updatePublishButton();
  if (!ebayCategory.value) {
    ebayRequirementsState = "idle";
    ebayRequirements.replaceChildren();
    ebayRequirements.hidden = true;
    setFreeSpecificEditing(true);
    updatePublishButton();
    return;
  }
  try {
    const response = await fetch(`/api/ebay/categories/${ebayCategory.value}/requirements`);
    const payload = await responsePayload(response, "eBay condition details failed.");
    if (!response.ok) throw new Error(payload.detail);
    if (
      currentDraftId !== draftId ||
      currentDraftPublished ||
      categoryRequirementsRevision !== loadRevision ||
      ebayCategory.value !== categoryId
    ) {
      return;
    }
    ebayCategoryRules = payload;
    ebayRequirementsState = "ready";
    ebayCondition.replaceChildren(new Option("Choose a condition", ""));
    payload.conditions.forEach((condition) => {
      ebayCondition.append(new Option(condition.name, String(condition.condition_id)));
    });
    ebayCondition.disabled = false;
    const conditionText = draftValue("condition").toLowerCase();
    const likelyCondition = payload.conditions.find((item) =>
      conditionText.includes(item.name.toLowerCase().split(" ")[0]),
    );
    const restoredCondition = payload.conditions.find(
      (item) => item.condition_id === savedEbayCondition?.condition_id,
    );
    if (restoredCondition || likelyCondition) {
      ebayCondition.value = String((restoredCondition || likelyCondition).condition_id);
    }
    if (reconcileNewGeneration) removeUnlistedSpecificRows();
    reconcileNewGeneration = false;
    ensureRequiredSpecificRows();
    applyAspectChoices();
    renderRequirements();
    if (!publicationRecoveryPending && (
      ebayCategory.value !== savedEbayCategory?.category_id ||
      ebayCondition.value !== String(savedEbayCondition?.condition_id || "")
    )) scheduleDraftSave();
  } catch (error) {
    if (
      currentDraftId !== draftId ||
      currentDraftPublished ||
      categoryRequirementsRevision !== loadRevision
    ) {
      return;
    }
    ebayRequirementsState = "error";
    ebayCategoryRules = null;
    ebayRequirements.textContent = error.message || "eBay condition details failed.";
    ebayRequirements.hidden = false;
    setFreeSpecificEditing(true);
    updatePublishButton();
  }
}

ebayCondition.addEventListener("change", () => {
  updatePublishButton();
  scheduleDraftSave();
});

function ensureRequiredSpecificRows() {
  const grid = draftFields.querySelector(".specifics-grid");
  if (!grid) return;
  const names = new Set(
    Array.from(grid.querySelectorAll("[data-specific-name]")).map((field) =>
      field.value.trim().toLowerCase(),
    ),
  );
  ebayCategoryRules.aspects
    .filter(
      (aspect) =>
        aspect.required &&
        !names.has(aspect.name.toLowerCase()),
    )
    .forEach((aspect) => {
      addSpecificRow(
        grid,
        { name: aspect.name, values: [], source: "unknown", confidence: "low" },
        grid.children.length,
      );
    });
}

function removeUnlistedSpecificRows() {
  const listedNames = new Set(
    ebayCategoryRules.aspects.map((aspect) => aspect.name.toLowerCase()),
  );
  draftFields.querySelectorAll(".specific-row").forEach((row) => {
    const name = row.querySelector("[data-specific-name]").value.trim().toLowerCase();
    if (!listedNames.has(name) && row.dataset.sellerEdited !== "true") row.remove();
  });
  reindexSpecificRows();
}

function setFreeSpecificEditing(enabled) {
  draftFields.querySelectorAll("[data-specific-name]").forEach((name) => {
    name.readOnly = !enabled;
  });
  if (enabled) {
    draftFields.querySelectorAll("datalist[data-ebay-aspect]").forEach((list) => list.remove());
    draftFields.querySelectorAll(".specific-row").forEach((row) => {
      row.querySelector(".confidence").textContent =
        `${row.dataset.source.replace("_", " ")} · ${row.dataset.confidence} confidence`;
      row.querySelector("[data-specific-remove]").hidden = false;
      const values = Array.from(row.querySelectorAll("[data-specific-value]"));
      values.forEach((value) => {
        value.removeAttribute("maxlength");
        value.removeAttribute("list");
        value.removeAttribute("inputmode");
        value.removeAttribute("aria-invalid");
        value.removeAttribute("aria-describedby");
        const length = value
          .closest(".specific-value-row")
          .querySelector("[data-specific-length]");
        length.hidden = true;
      });
      const addValue = row.querySelector(".specific-add-value");
      addValue.hidden = false;
      addValue.disabled = values.length >= 30;
      row.querySelectorAll(".specific-remove-value").forEach((button) => {
        button.hidden = values.length === 1;
      });
      const issue = row.querySelector("[data-specific-error]");
      issue.replaceChildren();
      issue.hidden = true;
    });
  }
  const picker = draftFields.querySelector("[data-specific-picker]");
  const custom = draftFields.querySelector("[data-custom-specific]");
  if (picker) {
    picker.hidden = enabled;
    if (enabled) picker.disabled = true;
  }
  if (custom) custom.hidden = !enabled;
}

function applyAspectChoices() {
  draftFields.querySelectorAll("datalist[data-ebay-aspect]").forEach((list) => list.remove());
  const currentValues = currentSpecificValues();
  const rows = Array.from(draftFields.querySelectorAll(".specific-row"));
  const rowNameCounts = new Map();
  rows.forEach((row) => {
    const name = row.querySelector("[data-specific-name]").value.trim().toLowerCase();
    rowNameCounts.set(name, (rowNameCounts.get(name) || 0) + 1);
  });
  rows.forEach((row, rowIndex) => {
    const nameField = row.querySelector("[data-specific-name]");
    nameField.readOnly = true;
    const name = nameField.value.trim().toLowerCase();
    const aspect = ebayCategoryRules.aspects.find(
      (item) => item.name.toLowerCase() === name,
    );
    if (aspect) nameField.value = aspect.name;
    const maxLength = aspect?.max_length || 65;
    const available = aspect ? availableAspectValues(aspect, currentValues) : [];
    const valueFields = Array.from(row.querySelectorAll("[data-specific-value]"));
    const valuesAreEmpty = valueFields.every((value) => !value.value.trim());
    const confidence = row.querySelector(".confidence");
    if (aspect?.required && valuesAreEmpty) {
      row.dataset.sellerAttributionPending = "true";
      confidence.textContent = "Required by eBay";
    } else {
      confidence.textContent = `${row.dataset.source.replace("_", " ")} · ${row.dataset.confidence} confidence`;
    }
    valueFields.forEach((value, valueIndex) => {
      value.removeAttribute("list");
      value.removeAttribute("inputmode");
      value.maxLength = maxLength;
      value.dataset.copyLabel = aspect?.name || nameField.value;
      const length = value.closest(".specific-value-row").querySelector("[data-specific-length]");
      length.textContent = `${value.value.length}/${maxLength}`;
      length.hidden = aspect?.mode !== "free_text";
      if (aspect?.data_type === "number" || aspect?.advanced_data_type === "numeric_range") {
        value.inputMode = "decimal";
      }
      if (aspect?.mode !== "selection" || !available.length) return;
      const list = document.createElement("datalist");
      list.id = `ebay-aspect-values-${rowIndex}-${valueIndex}`;
      list.dataset.ebayAspect = aspect.name;
      available.forEach((item) => list.append(new Option(item.value)));
      value.setAttribute("list", list.id);
      row.append(list);
    });
    const addValue = row.querySelector(".specific-add-value");
    addValue.hidden = aspect?.cardinality !== "multi";
    addValue.disabled = valueFields.length >= 30;
    row.querySelectorAll(".specific-remove-value").forEach((button) => {
      button.hidden = aspect?.cardinality !== "multi" || valueFields.length === 1;
    });
    row.querySelector("[data-specific-remove]").hidden = Boolean(
      aspect?.required && rowNameCounts.get(name) === 1,
    );
  });
  updateSpecificPicker();
}

function updateSpecificPicker() {
  const picker = draftFields.querySelector("[data-specific-picker]");
  if (!picker) return;
  picker.hidden = false;
  const custom = draftFields.querySelector("[data-custom-specific]");
  if (custom) custom.hidden = true;
  const used = new Set(
    Array.from(draftFields.querySelectorAll("[data-specific-name]")).map((field) =>
      field.value.trim().toLowerCase(),
    ),
  );
  const choices = ebayCategoryRules.aspects.filter(
    (aspect) => !used.has(aspect.name.toLowerCase()),
  );
  picker.replaceChildren(new Option("Add an eBay item specific", ""));
  choices.forEach((aspect) => {
    const suffix = aspect.required ? " — required" : aspect.recommended ? " — recommended" : "";
    picker.append(new Option(`${aspect.name}${suffix}`, aspect.name));
  });
  picker.disabled = !choices.length;
  picker.value = "";
}

function currentSpecificValues() {
  const values = new Map();
  draftFields.querySelectorAll(".specific-row").forEach((row) => {
    const name = row.querySelector("[data-specific-name]").value.trim().toLowerCase();
    const rowValues = Array.from(row.querySelectorAll("[data-specific-value]"))
      .map((field) => field.value.trim())
      .filter(Boolean);
    values.set(name, [...(values.get(name) || []), ...rowValues]);
  });
  return values;
}

function availableAspectValues(aspect, currentValues) {
  return aspect.values.filter(
    (item) =>
      !item.constraints.length ||
      item.constraints.every((constraint) => {
        const controlling = currentValues.get(constraint.aspect_name.toLowerCase()) || [];
        return controlling.some((value) => constraint.values.includes(value));
      }),
  );
}

function unsupportedAspectRule(aspect) {
  if (!["free_text", "selection"].includes(aspect.mode)) return `mode ${aspect.mode}`;
  if (!["single", "multi"].includes(aspect.cardinality)) {
    return `cardinality ${aspect.cardinality}`;
  }
  if (!["string", "number", "date"].includes(aspect.data_type)) {
    return `data type ${aspect.data_type}`;
  }
  if (!["optional", "recommended"].includes(aspect.usage)) return `usage ${aspect.usage}`;
  if (![null, "numeric_range"].includes(aspect.advanced_data_type)) {
    return `advanced data type ${aspect.advanced_data_type}`;
  }
  if (aspect.mode === "selection" && !aspect.values.length) return "selection rule";
  if (aspect.data_type === "string" && aspect.format) return `format ${aspect.format}`;
  if (aspect.data_type === "number" && ![null, "int32", "double"].includes(aspect.format)) {
    return `format ${aspect.format}`;
  }
  if (aspect.data_type === "date" && !["YYYY", "YYYYMM", "YYYYMMDD"].includes(aspect.format)) {
    return `format ${aspect.format || "(missing)"}`;
  }
  const applicability = Array.isArray(aspect.applicable_to)
    ? aspect.applicable_to
    : aspect.applicable_to
      ? [aspect.applicable_to]
      : [];
  if (applicability.some((item) => !["item", "product"].includes(item))) {
    return `applicability ${applicability.join(", ")}`;
  }
  return null;
}

function aspectValueHasValidFormat(aspect, value) {
  if (aspect.advanced_data_type === "numeric_range") {
    const match = value.match(/^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*-\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$/);
    return Boolean(match && Number(match[1]) <= Number(match[2]));
  }
  if (aspect.data_type === "string") return true;
  if (aspect.data_type === "number") {
    const number = Number(value);
    if (!Number.isFinite(number)) return false;
    if (aspect.format === "int32") {
      return Number.isInteger(number) && number >= -(2 ** 31) && number < 2 ** 31;
    }
    return true;
  }
  if (aspect.data_type === "date") {
    const patterns = { YYYY: /^\d{4}$/, YYYYMM: /^\d{6}$/, YYYYMMDD: /^\d{8}$/ };
    if (!patterns[aspect.format]?.test(value)) return false;
    const year = Number(value.slice(0, 4));
    const month = aspect.format === "YYYY" ? 1 : Number(value.slice(4, 6));
    const day = aspect.format === "YYYYMMDD" ? Number(value.slice(6, 8)) : 1;
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  }
  return false;
}

function itemSpecificErrors() {
  const rows = Array.from(draftFields.querySelectorAll(".specific-row"));
  const aspects = new Map(
    ebayCategoryRules.aspects.map((aspect) => [aspect.name.toLowerCase(), aspect]),
  );
  const currentValues = currentSpecificValues();
  const errors = [];
  if (rows.length > 45) {
    errors.push({ aspect: "Item specifics", code: "too_many_aspects", message: "Use no more than 45 item specifics." });
  }
  ebayCategoryRules.aspects.forEach((aspect) => {
    const unsupported = unsupportedAspectRule(aspect);
    if (unsupported) {
      errors.push({ aspect: aspect.name, code: "unsupported_rule", message: `eBay returned an unsupported ${unsupported} rule for ${aspect.name}.` });
    }
  });
  const counts = new Map();
  rows.forEach((row) => {
    const name = row.querySelector("[data-specific-name]").value.trim();
    const key = name.toLowerCase();
    counts.set(key, (counts.get(key) || 0) + 1);
    const aspect = aspects.get(key);
    const values = Array.from(row.querySelectorAll("[data-specific-value]")).map((field) =>
      field.value.trim(),
    );
    if (!aspect) {
      errors.push({ aspect: name, code: "unknown_aspect", message: `Remove ${name}. eBay does not list it for this category.` });
      return;
    }
    if (!values.length || values.some((value) => !value)) {
      errors.push({ aspect: aspect.name, code: "empty_value", message: `Enter a value for ${aspect.name}.` });
    }
    if (values.length > 30) {
      errors.push({ aspect: aspect.name, code: "too_many_values", message: `Use no more than 30 values for ${aspect.name}.` });
    }
    if (aspect.cardinality === "single" && values.length > 1) {
      errors.push({ aspect: aspect.name, code: "wrong_cardinality", message: `Use one value for ${aspect.name}.` });
    }
    if (new Set(values).size !== values.length) {
      errors.push({ aspect: aspect.name, code: "duplicate_value", message: `Do not repeat a value for ${aspect.name}.` });
    }
    const allowed = new Map(aspect.values.map((item) => [item.value, item]));
    values.filter(Boolean).forEach((value) => {
      const maxLength = aspect.max_length || 65;
      if (value.length > maxLength) {
        errors.push({ aspect: aspect.name, code: "value_too_long", message: `Keep each ${aspect.name} value at ${maxLength} characters or fewer.` });
      }
      if (aspect.mode === "selection" && !allowed.has(value)) {
        errors.push({ aspect: aspect.name, code: "invalid_value", message: `Choose an allowed value for ${aspect.name}.` });
      } else {
        const selected = allowed.get(value);
        if (selected?.constraints.length && !availableAspectValues(aspect, currentValues).includes(selected)) {
          errors.push({ aspect: aspect.name, code: "value_constraint", message: `${value} is not available with the selected related item specifics.` });
        }
      }
      if (!aspectValueHasValidFormat(aspect, value)) {
        errors.push({ aspect: aspect.name, code: "invalid_format", message: `Enter ${aspect.name} in eBay's required ${aspect.format || aspect.data_type} format.` });
      }
    });
  });
  counts.forEach((count, key) => {
    if (count > 1) {
      const aspect = aspects.get(key);
      const name = aspect?.name || key;
      errors.push({ aspect: name, code: "duplicate_aspect", message: `Use ${name} only once.` });
    }
  });
  ebayCategoryRules.aspects.forEach((aspect) => {
    if (!aspect.required || counts.has(aspect.name.toLowerCase())) return;
    errors.push({ aspect: aspect.name, code: "required", message: `Enter ${aspect.name}.` });
  });
  return errors.filter(
    (error, index) =>
      errors.findIndex(
        (item) => item.aspect === error.aspect && item.code === error.code && item.message === error.message,
      ) === index,
  );
}

function renderRequirements() {
  const specifics = currentSpecificValues();
  const errors = itemSpecificErrors();
  const missingRecommended = ebayCategoryRules.aspects
    .filter(
      (aspect) =>
        aspect.recommended &&
        !specifics.get(aspect.name.toLowerCase())?.length,
    )
    .map((aspect) => aspect.name);
  const futureRequired = ebayCategoryRules.aspects
    .filter(
      (aspect) =>
        !aspect.required &&
        aspect.expected_required_by_date &&
        !specifics.get(aspect.name.toLowerCase())?.length,
    )
    .map((aspect) => `${aspect.name} after ${aspect.expected_required_by_date}`);
  const messages = [];
  messages.push(...errors.map((error) => error.message));
  if (missingRecommended.length) {
    messages.push(`Recommended: ${missingRecommended.slice(0, 8).join(", ")}.`);
  }
  if (futureRequired.length) messages.push(`Expected to become required: ${futureRequired.join(", ")}.`);
  ebayRequirements.textContent = messages.join(" ");
  ebayRequirements.hidden = !messages.length;
  ebayRequirementsValid = !errors.length;
  Array.from(draftFields.querySelectorAll(".specific-row")).forEach((row) => {
    const name = row.querySelector("[data-specific-name]").value.trim();
    const rowErrors = errors.filter((error) => error.aspect.toLowerCase() === name.toLowerCase());
    row.querySelectorAll("[data-specific-value]").forEach((valueField) => {
      valueField.setAttribute("aria-invalid", String(Boolean(rowErrors.length)));
      valueField.setAttribute("aria-describedby", "ebay-requirements ebay-publish-readiness");
    });
    const issue = row.querySelector("[data-specific-error]");
    issue.textContent = rowErrors.map((error) => error.message).join(" ");
    issue.hidden = !rowErrors.length;
  });
  updatePublishButton();
}

function packageWeightProblem() {
  const pounds = packageWeightPounds.valueAsNumber;
  const ounces = packageWeightOunces.valueAsNumber;
  if (!Number.isInteger(pounds) || !Number.isInteger(ounces)) {
    return "Enter whole-number pounds and ounces.";
  }
  if (pounds < 0) return "Enter zero or more whole pounds.";
  if (ounces < 0 || ounces > 15) return "Enter 0 to 15 whole ounces.";
  if (pounds + ounces === 0) return "Enter a package weight greater than zero.";
  return null;
}

function invalidPackageDimensions() {
  return packageDimensionControls.filter(({ input }) => {
    const value = input.valueAsNumber;
    return !Number.isFinite(value) || value <= 0;
  });
}

function publishBlockers() {
  const blockers = [];
  if (currentDraft?.generation_required) {
    blockers.push("Category changed. Select Generate listing again before publication.");
  }
  if (!publicationRecoveryPending) {
    if (ebayCategoryState === "loading") {
      blockers.push("Wait for the eBay category to load.");
    } else if (ebayCategoryState === "error") {
      blockers.push("Reload the page because the eBay category could not load.");
    } else if (!ebayCategory.value) {
      blockers.push("Choose an eBay category.");
    }

    if (ebayCategory.value) {
      if (ebayRequirementsState === "loading") {
        blockers.push("Wait for the eBay category details to load.");
      } else if (ebayRequirementsState === "error") {
        blockers.push("Reload the page because the eBay category details could not load.");
      } else if (ebayRequirementsState === "ready" && !ebayRequirementsValid) {
        blockers.push("Complete the required eBay item specifics shown above.");
      }
      if (ebayRequirementsState === "ready" && !ebayCondition.value) {
        blockers.push("Choose an eBay condition.");
      }
    }

    if (ebayPoliciesState === "loading") {
      blockers.push("Wait for the eBay business policies to load.");
    } else if (ebayPoliciesState === "missing") {
      blockers.push("Create the missing eBay business policy shown above.");
    } else if (ebayPoliciesState === "error") {
      blockers.push("Reload the page because the eBay business policies could not load.");
    } else if (
      ebayPoliciesState === "ready" &&
      !Object.values(policyControls).every(({ select }) => select.value)
    ) {
      blockers.push("Choose the shipping, payment, and return policies.");
    }
  }

  const weightProblem = packageWeightProblem();
  if (weightProblem) blockers.push(weightProblem);
  invalidPackageDimensions().forEach(({ label }) => {
    blockers.push(`Enter a package ${label} greater than zero.`);
  });
  if (photoRecoveryExpectedCount !== null) {
    blockers.push(`Select all ${photoRecoveryExpectedCount} original photos.`);
  }
  return blockers;
}

function renderPublishReadiness(blockers) {
  ebayPublishReadiness.replaceChildren();
  if (!blockers.length) {
    ebayPublishReadiness.hidden = true;
    return;
  }
  const heading = document.createElement("p");
  heading.textContent = "Before you can publish:";
  const list = document.createElement("ul");
  blockers.forEach((blocker) => {
    const item = document.createElement("li");
    item.textContent = blocker;
    list.append(item);
  });
  ebayPublishReadiness.append(heading, list);
  ebayPublishReadiness.hidden = false;
}

function updatePublishButton() {
  if (currentDraftPublished) {
    sendToEbayButton.disabled = true;
    renderPublishReadiness([]);
    return;
  }
  const weightInvalid = Boolean(packageWeightProblem());
  const invalidDimensions = invalidPackageDimensions();
  packageWeightPounds.setAttribute("aria-invalid", String(weightInvalid));
  packageWeightOunces.setAttribute("aria-invalid", String(weightInvalid));
  invalidDimensions.forEach(({ input }) => {
    input.setAttribute("aria-invalid", "true");
  });
  packageDimensionControls
    .filter(({ input }) => !invalidDimensions.some((item) => item.input === input))
    .forEach(({ input }) => input.setAttribute("aria-invalid", "false"));

  ebayCategory.setAttribute(
    "aria-invalid",
    String(ebayCategoryState !== "loading" && !ebayCategory.value),
  );
  ebayCondition.setAttribute(
    "aria-invalid",
    String(ebayRequirementsState === "ready" && !ebayCondition.value),
  );
  Object.values(policyControls).forEach(({ select }) => {
    select.setAttribute(
      "aria-invalid",
      String(ebayPoliciesState === "ready" && !select.value),
    );
  });

  if (
    ebayConnectionState !== "ready" ||
    !ebayConnection?.connected ||
    !ebayConnection.direct_publish_enabled ||
    ebayPublishInFlight
  ) {
    sendToEbayButton.disabled = true;
    renderPublishReadiness([]);
    return;
  }
  const blockers = publishBlockers();
  sendToEbayButton.disabled = blockers.length > 0;
  renderPublishReadiness(blockers);
}

function packageWeightValue() {
  const pounds = packageWeightPounds.valueAsNumber;
  const ounces = packageWeightOunces.valueAsNumber;
  if (
    !Number.isInteger(pounds) ||
    pounds < 0 ||
    !Number.isInteger(ounces) ||
    ounces < 0 ||
    ounces > 15 ||
    pounds + ounces === 0
  ) {
    return null;
  }
  return { pounds, ounces };
}

function packageDimensionsValue() {
  const values = {};
  for (const { key, input } of packageDimensionControls) {
    const value = input.valueAsNumber;
    if (!Number.isFinite(value) || value <= 0) return null;
    values[key] = Math.ceil(value);
  }
  return values;
}

function renderPackageDimensionRoundingStatus() {
  const messages = Array.from(packageDimensionRoundingMessages.values());
  packageDimensionsStatus.textContent = messages.join(" ");
  packageDimensionsStatus.hidden = !messages.length;
}

function normalizePackageDimensions() {
  packageDimensionControls.forEach(({ announcement, input }) => {
    const value = input.valueAsNumber;
    if (!Number.isFinite(value) || value <= 0) return;
    const rounded = Math.ceil(value);
    if (rounded === value) return;
    input.value = String(rounded);
    packageDimensionRoundingMessages.set(
      input,
      `${announcement} was rounded up from ${value} to ${rounded} inches.`,
    );
  });
  renderPackageDimensionRoundingStatus();
}

function draftValue(key) {
  return draftFields.querySelector(`[data-draft-key="${key}"]`)?.value.trim() || "";
}

function listValue(key, emptyLabels = []) {
  const value = draftValue(key);
  return emptyLabels.includes(value) ? [] : value.split(/\n|,/).map((item) => item.trim()).filter(Boolean);
}

function buildSavedDraftUpdate() {
  const itemSpecifics = Array.from(draftFields.querySelectorAll(".specific-row"))
    .map((row) => {
      const values = Array.from(row.querySelectorAll("[data-specific-value]"))
        .map((field) => field.value.trim())
        .filter(Boolean);
      return {
        name: row.querySelector("[data-specific-name]").value.trim(),
        values,
        source: row.dataset.source || "seller_note",
        confidence: row.dataset.confidence || "high",
      };
    })
    .filter((item) => item.name && item.values.length);
  const selectedCategory = ebayCategory.selectedOptions[0];
  const selectedCondition = ebayCategoryRules?.conditions.find(
    (condition) => String(condition.condition_id) === ebayCondition.value,
  );
  return {
    revision: currentDraftRevision,
    draft: {
      ...currentDraft,
      title: draftValue("title"),
      suggested_category: draftValue("suggested_category"),
      search_terms: listValue("search_terms"),
      condition: draftValue("condition"),
      condition_description: draftValue("condition_description"),
      description: draftValue("description"),
      item_specifics: itemSpecifics,
      quantity: Number(draftValue("quantity")),
      recommended_listing_format: draftValue("recommended_listing_format").replace(" ", "_"),
      observed_flaws: listValue("observed_flaws", ["None clearly visible"]),
      missing_facts: listValue("missing_facts", ["None"]),
      pricing: {
        ...currentDraft.pricing,
        suggested_price: Number(draftValue("price")),
        expected_sale_price_low: Number(draftValue("expected_sale_price_low")),
        expected_sale_price_high: Number(draftValue("expected_sale_price_high")),
        confidence: draftValue("pricing_confidence"),
        rationale: draftValue("pricing_rationale"),
      },
      package_weight: packageWeightValue(),
      package_dimensions: packageDimensionsValue(),
    },
    ebay_category: ebayCategory.value
      ? {
          category_id: ebayCategory.value,
          group_id: selectedCategory?.dataset.groupId || null,
          name: selectedCategory?.dataset.name || selectedCategory?.textContent || "eBay category",
          path: selectedCategory?.textContent || "eBay category",
        }
      : null,
    ebay_condition: selectedCondition
      ? { condition_id: selectedCondition.condition_id, name: selectedCondition.name }
      : null,
    photo_count: currentPhotoCount,
  };
}

function scheduleDraftSave() {
  if (!currentDraftId || currentDraftPublished || draftOpenInFlight) return;
  saveRevision += 1;
  clearTimeout(saveTimer);
  draftSaveStatus.textContent = "Saving…";
  draftSaveMessage.hidden = true;
  saveTimer = setTimeout(saveCurrentDraft, 600);
}

function currentDraftSaveIsNoOp() {
  return (
    savedRevision >= saveRevision &&
    (currentPhotosStored ||
      (photoRecoveryExpectedCount !== null && selectedPhotos.length === 0))
  );
}

async function performCurrentDraftSave() {
  const draftId = currentDraftId;
  const deletionPending = await draftDeletionIsPending(draftId);
  if (deletionPending === null && currentDraftRevision !== null) {
    draftSaveStatus.textContent = "Not saved";
    saveDraftButton.hidden = false;
    draftSaveMessage.textContent =
      "This browser could not check pending draft deletions. Keep this page open and try Save now again.";
    draftSaveMessage.hidden = false;
    return false;
  }
  if (deletionPending) {
    if (currentDraftId === draftId) {
      closeCurrentDraftAfterDeletion();
      loadSavedDrafts();
    }
    return false;
  }
  if (
    photoRecoveryExpectedCount !== null &&
    selectedPhotos.length > 0 &&
    selectedPhotos.length !== photoRecoveryExpectedCount
  ) {
    return false;
  }
  normalizePackageDimensions();
  updatePublishButton();
  if (savedRevision >= saveRevision) {
    if (!currentPhotosStored && photoRecoveryExpectedCount !== null) {
      return selectedPhotos.length === 0;
    }
    return currentPhotosStored ? true : persistCurrentDraftPhotos();
  }
  const revision = saveRevision;
  const previousDraftRevision = currentDraftRevision;
  const savedPhotoRevision = photoRevision;
  const photosStoredAtStart = currentPhotosStored;
  const body = buildSavedDraftUpdate();
  const response = await fetch(`/api/drafts/${draftId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await responsePayload(response, "We could not save this draft.");
  if (!response.ok) throw new Error(payload.detail || "We could not save this draft.");
  currentDraft = {
    ...payload.draft,
    generation_required: Boolean(currentDraft?.generation_required || payload.draft.generation_required),
  };
  updateGenerationReadiness();
  currentDraftRevision = payload.revision;
  savedEbayCategory = payload.ebay_category;
  savedEbayCondition = payload.ebay_condition;
  savedRevision = revision;
  draftSaveStatus.textContent = "Saved";
  saveDraftButton.hidden = true;
  draftSaveMessage.textContent = "";
  draftSaveMessage.hidden = true;
  let photoPersistenceSucceeded = true;
  if (
    photosStoredAtStart &&
    currentDraftId === draftId &&
    photoRevision === savedPhotoRevision
  ) {
    try {
      await advanceDraftPhotoRevision(
        photoDatabase,
        currentUserId,
        draftId,
        previousDraftRevision,
        payload.revision,
      );
      currentPhotosStored = currentDraftId === draftId && photoRevision === savedPhotoRevision;
      if (currentPhotosStored) photoReselectMessage.hidden = true;
    } catch (error) {
      photoPersistenceSucceeded = false;
      currentPhotosStored = false;
      photoReselectMessage.textContent =
        error.name === "StaleDraftPhotoWriteError"
          ? "A newer version of this draft already saved photos in this browser. Reload the draft before you continue."
          : "The draft was saved, but this browser could not update its stored photos. Keep the originals and select them again before publishing.";
      photoReselectMessage.hidden = false;
    }
  } else {
    currentPhotosStored = false;
    photoPersistenceSucceeded = await persistCurrentDraftPhotos();
  }
  loadSavedDrafts();
  return photoPersistenceSucceeded;
}

async function saveCurrentDraft() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (!currentDraftId) return false;
  if (currentDraftPublished) return true;
  if (saveInFlight) {
    const activeSave = saveInFlight;
    const activeRevision = saveInFlightRevision;
    const completed = await activeSave;
    if (saveRevision > activeRevision) {
      if (saveInFlight === activeSave) {
        saveInFlight = null;
        saveInFlightRevision = null;
      }
      const newerCompleted = await saveCurrentDraft();
      return completed && newerCompleted;
    }
    return completed;
  }
  const activeRevision = saveRevision;
  const activeSave = performCurrentDraftSave().catch((error) => {
    draftSaveStatus.textContent = "Not saved";
    saveDraftButton.hidden = false;
    draftSaveMessage.textContent =
      error.message || "We could not save this draft. Keep this page open and try Save now again.";
    draftSaveMessage.hidden = false;
    return false;
  });
  saveInFlight = activeSave;
  saveInFlightRevision = activeRevision;
  try {
    return await activeSave;
  } finally {
    if (saveInFlight === activeSave) {
      saveInFlight = null;
      saveInFlightRevision = null;
    }
  }
}

draftFields.addEventListener("input", scheduleDraftSave);
saveDraftButton.addEventListener("click", saveCurrentDraft);

function freezePublicationControls() {
  generationCategorySearchRevision += 1;
  const controls = [
    input,
    notes,
    ...generationCategoryControls,
    generateButton,
    ...photoList.querySelectorAll("button"),
    ...resultSection.querySelectorAll("input, textarea, select, button"),
  ];
  controls.forEach((control) => {
    if (!publicationDisabledControls.has(control)) {
      publicationDisabledControls.set(control, control.disabled);
    }
    control.disabled = true;
  });
}

function restorePublicationControls(keepPoliciesFrozen = false) {
  publicationDisabledControls.forEach((disabled, control) => {
    control.disabled = disabled;
  });
  publicationDisabledControls.clear();
  if (keepPoliciesFrozen) {
    Object.values(policyControls).forEach(({ select }) => {
      select.disabled = true;
    });
  }
}

sendToEbayButton.addEventListener("click", async () => {
  clearError();
  const publicationDraftId = currentDraftId;
  ebayPublishInFlight = true;
  setSavedDraftActionsDisabled(true);
  freezePublicationControls();
  updatePublishButton();
  if (!(await saveCurrentDraft())) {
    ebayTransferStatus.hidden = false;
    ebayTransferStatus.textContent =
      !currentPhotosStored && !photoReselectMessage.hidden
        ? photoReselectMessage.textContent
        : "Save this draft before you publish it.";
    ebayPublishInFlight = false;
    setSavedDraftActionsDisabled(false);
    restorePublicationControls(publicationRecoveryPending);
    updateGenerationReadiness();
    updatePublishButton();
    return;
  }
  if (currentDraftId !== publicationDraftId) {
    ebayPublishInFlight = false;
    setSavedDraftActionsDisabled(false);
    restorePublicationControls(publicationRecoveryPending);
    updateGenerationReadiness();
    showError("The open draft changed before publication started. Review it and try again.");
    updatePublishButton();
    return;
  }
  if (!currentPhotosStored) {
    ebayTransferStatus.hidden = false;
    ebayTransferStatus.textContent =
      photoReselectMessage.textContent ||
      "This browser could not bind these photos to the saved draft. Reload the draft before you publish it.";
    ebayPublishInFlight = false;
    setSavedDraftActionsDisabled(false);
    restorePublicationControls(publicationRecoveryPending);
    updateGenerationReadiness();
    updatePublishButton();
    return;
  }
  ebayTransferStatus.hidden = false;
  ebayTransferStatus.textContent =
    "Saving photos, checking eBay fields, and publishing. Keep this page open.";
  sendToEbayButton.disabled = true;
  let publishRequestStarted = false;
  try {
    let prepared = await prepareLiveListing(false);
    if (!prepared.response.ok && prepared.payload.code === "legacy_seller_hub_check_required") {
      const confirmed = window.confirm(
        "Before publishing, confirm that this item does not already exist in Seller Hub Drafts.",
      );
      if (!confirmed) throw new Error("Publication cancelled. Check Seller Hub Drafts first.");
      prepared = await prepareLiveListing(true);
    }
    if (!prepared.response.ok) {
      if (prepared.payload.code === "invalid_item_specifics") {
        await refreshItemSpecificErrors(prepared.payload.errors || []);
      }
      const error = new Error(
        prepared.payload.detail || "The eBay listing could not be prepared.",
      );
      if (prepared.payload.code === "publication_in_progress") {
        error.publicationRecoveryPending = true;
      }
      if (["draft_changed", "draft_not_found", "seller_hub_draft_exists", "generation_required"].includes(prepared.payload.code)) {
        error.publicationRecoveryPending = false;
      }
      throw error;
    }
    publicationRecoveryPreparationId = prepared.payload.preparation_id;
    publishRequestStarted = true;
    const response = await fetch(`/api/ebay/listings/${prepared.payload.listing_id}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft_revision: prepared.payload.draft_revision,
        preparation_id: prepared.payload.preparation_id,
      }),
    });
    const payload = await responsePayload(response, "eBay did not confirm publication.");
    if (!response.ok) {
      const error = new Error(payload.detail || "eBay did not confirm publication.");
      const protectedCodes = [
        "ebay_publish_uncertain",
        "ebay_photos_expired_recovery",
        "ebay_publication_failed_recovery",
        "ebay_verification_unavailable_recovery",
        "ebay_verification_failed_recovery",
        "publication_in_progress",
      ];
      const definiteCodes = [
        "draft_changed",
        "ebay_photos_expired",
        "ebay_publication_failed",
        "ebay_verification_failed",
        "ebay_verification_unavailable",
        "seller_hub_draft_exists",
      ];
      if (protectedCodes.includes(payload.code)) error.publicationRecoveryPending = true;
      if (definiteCodes.includes(payload.code)) error.publicationRecoveryPending = false;
      throw error;
    }
    if (currentDraftId === publicationDraftId) {
      showPublication(payload);
    } else {
      ebayPublishInFlight = false;
      setSavedDraftActionsDisabled(false);
      restorePublicationControls();
      loadSavedDrafts();
      showError(
        "The listing was published, but another draft is now open. Open the published draft from Saved drafts to view it.",
      );
      updatePublishButton();
    }
  } catch (error) {
    ebayTransferStatus.textContent = error.message || "The eBay listing could not be published.";
    sendToEbayButton.textContent = "Retry publish";
    const recoveryPending =
      error.publicationRecoveryPending ?? (publicationRecoveryPending || publishRequestStarted);
    publicationRecoveryPending = recoveryPending;
    if (!recoveryPending) {
      publicationRecoveryPolicies = null;
      publicationRecoveryPreparationId = null;
    }
    ebayPublishInFlight = false;
    setSavedDraftActionsDisabled(false);
    restorePublicationControls(recoveryPending);
    updateGenerationReadiness();
    updatePublishButton();
  }
});

async function refreshItemSpecificErrors(errors) {
  await loadCategoryRequirements();
  if (ebayRequirementsState === "ready") return;
  ebayRequirementsValid = false;
  ebayRequirements.textContent = errors.map((error) => error.message).join(" ");
  ebayRequirements.hidden = !errors.length;
  draftFields.querySelectorAll(".specific-row").forEach((row) => {
    const name = row.querySelector("[data-specific-name]").value.trim().toLowerCase();
    const rowErrors = errors.filter((error) => error.aspect.toLowerCase() === name);
    row.querySelectorAll("[data-specific-value]").forEach((field) => {
      field.setAttribute("aria-invalid", String(Boolean(rowErrors.length)));
    });
    const issue = row.querySelector("[data-specific-error]");
    issue.textContent = rowErrors.map((error) => error.message).join(" ");
    issue.hidden = !rowErrors.length;
  });
  updatePublishButton();
}

async function prepareLiveListing(confirmNoEbayDraft) {
  const formData = new FormData();
  formData.append("draft_id", currentDraftId);
  formData.append("draft_revision", currentDraftRevision);
  formData.append("fulfillment_policy_id", policyControls.fulfillment_policy.select.value);
  formData.append("payment_policy_id", policyControls.payment_policy.select.value);
  formData.append("return_policy_id", policyControls.return_policy.select.value);
  formData.append("package_weight_pounds", packageWeightPounds.value);
  formData.append("package_weight_ounces", packageWeightOunces.value);
  formData.append("package_length", packageLength.value);
  formData.append("package_width", packageWidth.value);
  formData.append("package_height", packageHeight.value);
  if (confirmNoEbayDraft) formData.append("confirm_no_ebay_draft", "true");
  if (publicationRecoveryPreparationId) {
    formData.append("recovery_preparation_id", publicationRecoveryPreparationId);
  }
  selectedPhotos.forEach((photo) => formData.append("photos", photo, photo.name));
  const response = await fetch("/api/ebay/live-listing-preparations", {
    method: "POST",
    body: formData,
  });
  const payload = await responsePayload(response, "The eBay listing could not be prepared.");
  return { response, payload };
}

function showPublication(payload) {
  ebayPublishInFlight = false;
  setSavedDraftActionsDisabled(false);
  publicationDisabledControls.clear();
  currentDraftPublished = true;
  publicationRecoveryPending = false;
  publicationRecoveryPolicies = null;
  publicationRecoveryPreparationId = null;
  input.disabled = true;
  generateButton.disabled = true;
  startNewListingButton.hidden = false;
  draftSaveStatus.textContent = "Live on eBay";
  saveDraftButton.hidden = true;
  sendToEbayButton.disabled = true;
  sendToEbayButton.textContent = "Published";
  loadSavedDrafts();
  ebayTransferStatus.replaceChildren(document.createTextNode("Listing published. "));
  const listing = document.createElement("a");
  listing.href = payload.listing_url;
  listing.target = "_blank";
  listing.rel = "noopener";
  listing.textContent = "View listing";
  const sellerHub = document.createElement("a");
  sellerHub.href = payload.seller_hub_url;
  sellerHub.target = "_blank";
  sellerHub.rel = "noopener";
  sellerHub.textContent = "Open Seller Hub active listings";
  ebayTransferStatus.append(
    listing,
    document.createTextNode(" or "),
    sellerHub,
    document.createTextNode(" to edit it."),
  );
  publishedDraftMessage.hidden = false;
  publishedDraftMessage.textContent =
    "This draft is published and read-only. It and its device-local photos remain here until you delete the draft.";
  resultSection.querySelectorAll("input, textarea, select, button").forEach((control) => {
    control.disabled = true;
  });
  copyButton.disabled = false;
  startNewListingButton.disabled = false;
  renderPhotos();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  if (!selectedPhotos.length) {
    showError("Choose at least one photo.");
    return;
  }
  const categoryForGeneration = selectedGenerationCategory();
  if (!categoryForGeneration) {
    showError("Select an eBay category before generating.");
    return;
  }
  if (generationInFlight || draftOpenInFlight || draftDeletionInFlight || ebayPublishInFlight || currentDraftPublished) return;
  generationInFlight = true;
  generationCategorySearchRevision += 1;
  invalidateEbayDraftLoaders();
  setLoading(true);
  freezeGenerationControls();
  const openDraftId = currentDraftId;
  if (openDraftId) {
    await saveCurrentDraft();
    if (currentDraftId !== openDraftId) {
      finishGeneration();
      showError("This draft is being deleted in another tab. Wait for cleanup to finish.");
      return;
    }
  }
  const generationDraftId = currentDraftId;
  const generationPhotoRevision = photoRevision;
  const generationPhotos = [...selectedPhotos];

  if (photoDatabase && photoStorageAvailable) {
    const stats = await refreshPhotoStorageDisplay();
    if (photoStorageAvailable) {
      const newBytes = selectedPhotoBytes(generationPhotos);
      if (stats.totalBytes + newBytes > PHOTO_STORAGE_LIMIT) {
        showError(
          "These photos would exceed the 1 GB draft photo limit in this browser. Delete drafts before you create another draft.",
        );
        finishGeneration();
        return;
      }
      try {
        if (!(await browserHasRoomFor(newBytes))) {
          showError(
            "This browser does not have enough available device storage for these draft photos. Delete drafts or free device storage before you continue.",
          );
          finishGeneration();
          return;
        }
      } catch {
        // The exact browser quota is optional. IndexedDB still reports a write failure safely.
      }
    }
  }

  if (
    currentDraftId !== generationDraftId ||
    photoRevision !== generationPhotoRevision
  ) {
    showError(
      "The open draft or photo selection changed before generation started. Review the open draft and try again.",
    );
    finishGeneration();
    return;
  }
  let generationReservationId = null;
  if (photoDatabase && photoStorageAvailable) {
    generationReservationId = crypto.randomUUID();
    try {
      await reservePhotoCapacity(
        photoDatabase,
        generationReservationId,
        selectedPhotoBytes(generationPhotos),
      );
    } catch (error) {
      if (error.name === "PhotoStorageLimitError") {
        showError(
          "These photos would exceed the 1 GB draft photo limit in this browser. Delete drafts before you create another draft.",
        );
        finishGeneration();
        return;
      }
      photoStorageAvailable = false;
      generationReservationId = null;
    }
  }

  const formData = new FormData();
  generationPhotos.forEach((photo) => formData.append("photos", photo, photo.name));
  formData.append("notes", notes.value);
  formData.append("ebay_category", JSON.stringify(categoryForGeneration));

  try {
    const response = await fetch("/api/listings/generate", { method: "POST", body: formData });
    const payload = await responsePayload(response, "We could not generate this listing. Please try again.");
    if (!response.ok) {
      if (response.status === 401) {
        window.location.assign("/login");
        return;
      }
      throw new Error(
        payload.detail || "We could not generate this listing. Please try again.",
      );
    }
    if (
      currentDraftId !== generationDraftId ||
      photoRevision !== generationPhotoRevision
    ) {
      if (payload.saved && photoDatabase && currentUserId !== null) {
        try {
          await saveDraftPhotos(
            photoDatabase,
            currentUserId,
            payload.draft_id,
            generationPhotos,
            payload.draft_revision,
            generationReservationId,
          );
        } catch {
          // The generated server draft remains available if local photo storage fails.
        }
      }
      if (payload.saved) loadSavedDrafts();
      await refreshPhotoStorageDisplay();
      showError(
        "The open draft or photo selection changed during generation. The generated draft was saved separately; open it from Saved drafts.",
      );
      return;
    }
    renderDraft(payload);
    if (payload.saved) await persistCurrentDraftPhotos(generationReservationId);
    await refreshPhotoStorageDisplay();
    if (payload.saved) loadSavedDrafts();
  } catch (error) {
    showError(error.message || "We could not generate this listing. Please try again.");
  } finally {
    if (generationReservationId && photoDatabase) {
      try {
        await releasePhotoCapacity(photoDatabase, generationReservationId);
      } catch {
        // Expired reservations are removed by a later capacity check.
      }
    }
    finishGeneration();
  }
});

copyButton.addEventListener("click", async () => {
  const lines = Array.from(resultSection.querySelectorAll("[data-copy-label]"))
    .map((field) => `${field.dataset.copyLabel}: ${field.value.trim()}`)
    .filter((line) => !line.endsWith(": "));
  try {
    await navigator.clipboard.writeText(lines.join("\n\n"));
    copyButton.textContent = "Copied";
    setTimeout(() => {
      copyButton.textContent = "Copy listing";
    }, 1600);
  } catch {
    showError("Copy was blocked by the browser. Select the listing text and copy it manually.");
  }
});
