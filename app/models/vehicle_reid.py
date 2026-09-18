import numpy as np
import cv2
import torch
import torchvision.models as models
import torchvision.transforms as T


class VehicleReIDExtractor:
    """Extracts 512-d visual embeddings from vehicle crops for Re-ID."""

    def __init__(self, device: str = "cuda"):
        self.device = torch.device(
            device if (device == "cuda" and torch.cuda.is_available()) else "cpu"
        )

        # Load ResNet-18 backbone (outputs 512 features)
        self.model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        self.model.fc = torch.nn.Identity()
        self.model.to(self.device)
        self.model.eval()

        self.transform = T.Compose(
            [
                T.ToPILImage(),
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )

    @torch.no_grad()
    def extract(self, vehicle_crop: np.ndarray) -> np.ndarray:
        """Returns 512-d L2-normalized feature vector."""
        if vehicle_crop is None or vehicle_crop.size == 0:
            return np.zeros(512, dtype=np.float32)

        rgb_crop = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2RGB)
        tensor = self.transform(rgb_crop).unsqueeze(0).to(self.device)

        features = self.model(tensor).squeeze(0).cpu().numpy()

        # L2 Normalization
        norm = np.linalg.norm(features)
        if norm > 0:
            features = features / norm

        return features.astype(np.float32)
