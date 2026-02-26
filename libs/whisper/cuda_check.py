import torch

if torch.cuda.is_available():
    print("CUDA is available. The device is supported.")
    print(f"Number of GPUs available: {torch.cuda.device_count()}")
    print(f"GPU Name: {torch.cuda.get_device_name(0)}")
    print(f"PyTorch compiled with CUDA version: {torch.version.cuda}")
    
    # Example of running a tensor on the GPU
    x = torch.randn(3, 3)
    x = x.to('cuda')
    print("Tensor on GPU:", x)
else:
    print("CUDA is not available. Falling back to CPU.")
