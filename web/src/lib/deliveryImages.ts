import type { DeliveryImage } from "../types";

export function recordedImages(images: DeliveryImage[] | undefined, image = "", digest = ""): DeliveryImage[] {
  if (images?.length) return images;
  if (!image && !digest) return [];
  return [{ build_key: "legacy", build: "", os: "", arch: "", python_label: "", pytorch_label: "", image_target: "",
    image, digest, reference: image && digest ? `${image.split("@")[0]}@${digest}` : image }];
}
